"""Integration tests against a real exported obfuscated vault.

These load a JSON snapshot of an *actual* Obsidian LiveSync CouchDB database
produced by the real plugin with **E2EE + Path Obfuscation + Property
Encryption** all enabled, and validate that our reverse-engineered algorithms
reproduce the plugin's output byte-for-byte:

  * path → ``f:`` document-ID obfuscation hashing
  * HKDF chunk decryption (shared with the encrypted vault)
  * encrypted (``h:+``) chunk-ID hashing
  * V3 Rabin-Karp splitter parity on the decrypted content

Fixture: mcp_server/fixtures/obfuscated/

Important structural note discovered from the real export: with Property
Encryption on, each note doc's top-level fields are zeroed (``children: []``,
``mtime: 0`` …) and the real metadata — including the real ``path`` and the
chunk ``children`` — is an **encrypted JSON blob stored in the ``path`` field**
(prefixed with ``/\\:`` then an HKDF ``%=`` ciphertext). The lower-level tests
decrypt that blob directly to validate the algorithms; the
``test_repository_*`` tests then exercise the real ``NoteRepository``, which
now auto-detects and decrypts that blob so ``read()``/``list_paths()`` work
transparently against a fully obfuscated + Property-Encrypted vault.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.errors import EncryptedVaultError
from obsidian_livesync_mcp.livesync import encryption
from obsidian_livesync_mcp.livesync.chunks import (
    hash_chunk,
    hashed_passphrase,
    split_pieces_rabin_karp,
)
from obsidian_livesync_mcp.livesync.links import LinkGraph
from obsidian_livesync_mcp.livesync.models import extract_frontmatter
from obsidian_livesync_mcp.livesync.notes import NoteRepository, _decode_chunk_payload
from obsidian_livesync_mcp.livesync.paths import path_to_id

from .fixture_loader import LoadedVault, load_vault

VAULT_NAME = "obfuscated"
PASSPHRASE = "ThisIsMyObsidianNotebook1234"
# Per-vault salt the plugin mixes into encrypted chunk IDs.
HASHED_PASSPHRASE = hashed_passphrase(PASSPHRASE)

# Skip the whole module cleanly if the fixture export isn't present.
try:
    _VAULT = load_vault(VAULT_NAME)
except FileNotFoundError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"obfuscated vault fixture missing: {exc}", allow_module_level=True)

# Guard: this suite validates *path obfuscation*. If the export was taken before
# obfuscation was applied (no `f:` document IDs), skip with a clear reason. Flips
# on automatically once a genuinely obfuscated export is supplied.
if not any(doc_id.startswith("f:") for doc_id in _VAULT.raw_docs):  # pragma: no cover
    pytest.skip(
        "obfuscated fixture has no f: document IDs — not actually obfuscated; "
        "re-export with Path Obfuscation enabled",
        allow_module_level=True,
    )


@pytest.fixture
def vault() -> LoadedVault:
    # Re-load per test so the fake CouchDB starts clean (writes mutate it).
    return load_vault(VAULT_NAME)


# --------------------------------------------------------------------------
# Helpers: decrypt the Property-Encryption metadata blob
# --------------------------------------------------------------------------


def _is_encrypted_metadata(path_field: object) -> bool:
    return isinstance(path_field, str) and "%=" in path_field


def _decrypt_metadata(doc: dict[str, Any], salt: bytes) -> dict[str, Any]:
    """Decrypt a Property-Encrypted note doc's metadata blob.

    The real metadata (path, mtime, ctime, size, children) is stored as an
    HKDF ciphertext in the ``path`` field, behind a ``/\\:`` marker. We locate
    the ``%=`` HKDF marker and decrypt from there.
    """
    raw = doc["path"]
    marker = raw.index("%=")
    decoded = encryption.decrypt(raw[marker:], PASSPHRASE, salt)
    meta: dict[str, Any] = json.loads(decoded)
    return meta


def _live_obfuscated_docs(vault: LoadedVault) -> list[dict[str, Any]]:
    return [
        doc
        for doc_id, doc in vault.raw_docs.items()
        if doc_id.startswith("f:")
        and doc.get("type") in ("plain", "newnote")
        and doc.get("deleted") is not True
    ]


def _assemble_content(meta: dict[str, Any], vault: LoadedVault) -> str:
    """Decrypt + concatenate a note's chunks from its decrypted children list."""
    assert vault.salt is not None
    parts = []
    for child_id in meta["children"]:
        chunk = vault.raw_docs[child_id]
        parts.append(
            _decode_chunk_payload(chunk["data"], passphrase=PASSPHRASE, pbkdf2_salt=vault.salt)
        )
    return "".join(parts)


def _children_encrypted(meta: dict[str, Any]) -> bool:
    children = meta.get("children") or []
    return bool(children) and isinstance(children[0], str) and children[0].startswith("h:+")


# --------------------------------------------------------------------------
# Sanity
# --------------------------------------------------------------------------


def test_obfuscated_vault_has_salt_and_protocol(vault: LoadedVault) -> None:
    assert vault.salt is not None, "obfuscated vault requires PBKDF2 salt"
    assert isinstance(vault.salt, bytes) and len(vault.salt) > 0
    assert vault.protocol_version == 2


def test_property_encryption_metadata_is_decryptable(vault: LoadedVault) -> None:
    """Each obfuscated note stores its real metadata as an encrypted blob."""
    assert vault.salt is not None
    docs = _live_obfuscated_docs(vault)
    assert len(docs) >= 2, "expected at least a couple of obfuscated notes"
    for doc in docs:
        assert _is_encrypted_metadata(doc["path"]), "path field should be an encrypted blob"
        meta = _decrypt_metadata(doc, vault.salt)
        assert isinstance(meta.get("path"), str) and meta["path"]
        assert isinstance(meta.get("children"), list)


# --------------------------------------------------------------------------
# Path obfuscation parity: path_to_id() reproduces the plugin's f: IDs
# --------------------------------------------------------------------------


def test_obfuscation_path_to_id_matches_plugin(vault: LoadedVault) -> None:
    """``path_to_id`` must reproduce the plugin's ``f:`` IDs.

    The real path lives inside the encrypted metadata blob; we decrypt it, hash
    it with ``path_to_id(obfuscate=True)`` and require the result to equal the
    document's actual ``_id``. This is the strongest obfuscation-hash signal.
    """
    assert vault.salt is not None
    mismatches: list[tuple[str, str, str]] = []
    for doc in _live_obfuscated_docs(vault):
        meta = _decrypt_metadata(doc, vault.salt)
        real_path = meta["path"]
        our_id = path_to_id(real_path, obfuscate=True, passphrase=PASSPHRASE, case_sensitive=False)
        if our_id != doc["_id"]:
            mismatches.append((real_path, doc["_id"], our_id))
    assert not mismatches, f"{len(mismatches)} obfuscated paths did not reproduce:\n" + "\n".join(
        f"  {path}: plugin={pid} ours={oid}" for path, pid, oid in mismatches[:5]
    )


def test_obfuscation_is_case_insensitive(vault: LoadedVault) -> None:
    """Any casing of a real path hashes to the same plugin ``f:`` id."""
    assert vault.salt is not None
    doc = _live_obfuscated_docs(vault)[0]
    real_path = _decrypt_metadata(doc, vault.salt)["path"]
    for variant in (real_path, real_path.upper(), real_path.lower()):
        assert (
            path_to_id(variant, obfuscate=True, passphrase=PASSPHRASE, case_sensitive=False)
            == doc["_id"]
        )


# --------------------------------------------------------------------------
# Encryption parity (shared with the encrypted vault)
# --------------------------------------------------------------------------


def test_chunk_payload_decrypts_in_obfuscated_vault(vault: LoadedVault) -> None:
    assert vault.salt is not None
    failures = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        try:
            decoded = _decode_chunk_payload(
                doc["data"], passphrase=PASSPHRASE, pbkdf2_salt=vault.salt
            )
            assert isinstance(decoded, str)
        except Exception as e:
            failures.append(f"{doc_id}: {e}")
    assert not failures, f"{len(failures)} chunks failed to decrypt: {failures[:5]}"


def test_obfuscated_chunk_ids_reproduced_by_hash_chunk(vault: LoadedVault) -> None:
    """hash_chunk(decode(data)) must match plugin chunk IDs (per-marker)."""
    assert vault.salt is not None
    mismatches = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        try:
            raw = _decode_chunk_payload(doc["data"], passphrase=PASSPHRASE, pbkdf2_salt=vault.salt)
            if (
                hash_chunk(
                    raw,
                    encrypted=doc_id.startswith("h:+"),
                    hashed_passphrase=HASHED_PASSPHRASE,
                )
                != doc_id
            ):
                mismatches.append(doc_id)
        except Exception:
            mismatches.append(doc_id)
    assert not mismatches, f"{len(mismatches)} chunk IDs did not reproduce: {mismatches[:5]}"


# --------------------------------------------------------------------------
# Reading & reassembly (via decrypted metadata) + V3 splitter parity
# --------------------------------------------------------------------------


def test_readme_decrypts_and_reassembles(vault: LoadedVault) -> None:
    """Find README by its decrypted path, reassemble its chunks, check content."""
    assert vault.salt is not None
    for doc in _live_obfuscated_docs(vault):
        meta = _decrypt_metadata(doc, vault.salt)
        if meta["path"] == "README.md":
            content = _assemble_content(meta, vault)
            assert "Fixture Vault Documentation" in content
            frontmatter, _ = extract_frontmatter(content)
            assert frontmatter.get("title") == "Fixture Vault Documentation"
            return
    pytest.fail("README.md not found among obfuscated notes")


def test_v3_splitter_reproduces_obfuscated_every_note(vault: LoadedVault) -> None:
    """Re-splitting each note's decrypted content must reproduce its children."""
    assert vault.salt is not None
    failures: list[str] = []
    for doc in _live_obfuscated_docs(vault):
        meta = _decrypt_metadata(doc, vault.salt)
        if not meta.get("children"):
            continue
        content = _assemble_content(meta, vault)
        enc = _children_encrypted(meta)
        ours = [
            hash_chunk(p, encrypted=enc, hashed_passphrase=HASHED_PASSPHRASE)
            for p in split_pieces_rabin_karp(content)
        ]
        if ours != meta["children"]:
            failures.append(f"{meta['path']} (plugin={len(meta['children'])} ours={len(ours)})")
    assert not failures, f"{len(failures)} notes did not reproduce: {failures[:5]}"


def test_link_graph_builds_from_obfuscated_vault(vault: LoadedVault) -> None:
    """Wikilinks in decrypted content feed the link graph."""
    assert vault.salt is not None
    graph = LinkGraph()
    notes: dict[str, str] = {}
    for doc in _live_obfuscated_docs(vault):
        meta = _decrypt_metadata(doc, vault.salt)
        if meta.get("children"):
            notes[meta["path"]] = _assemble_content(meta, vault)
    graph.rebuild_from_notes(notes)
    assert isinstance(graph, LinkGraph)


# --------------------------------------------------------------------------
# NoteRepository transparent read/list of Property-Encrypted vaults
#
# These exercise the real repository end-to-end: it must auto-detect the
# Property-Encryption metadata blob, decrypt it, and expose real paths and
# reassembled content just like a plain vault.
# --------------------------------------------------------------------------


@pytest.fixture
def repo(vault: LoadedVault) -> NoteRepository:
    assert vault.salt is not None, "obfuscated vault must have PBKDF2 salt"
    return NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=vault.salt,
        obfuscate_paths=True,
        case_sensitive=False,
    )


async def test_repository_reads_property_encrypted_readme(repo: NoteRepository) -> None:
    """``read`` resolves the real path, decrypts metadata + chunks transparently."""
    note = await repo.read("README.md")
    assert note is not None
    assert note.path == "README.md"
    assert "Fixture Vault Documentation" in note.content
    assert note.mtime > 0  # real mtime came from the decrypted metadata blob
    assert note.size > 0


async def test_repository_reads_property_encrypted_subfolder_note(repo: NoteRepository) -> None:
    """A note in a subfolder (path inside the encrypted blob) reads back."""
    note = await repo.read("Guides/Link notes.md")
    assert note is not None
    assert note.path == "Guides/Link notes.md"
    assert note.content  # non-empty: children were decrypted from the blob


async def test_repository_read_is_case_insensitive_property_encrypted(
    repo: NoteRepository,
) -> None:
    for variant in ("README.md", "readme.md", "ReadMe.MD"):
        note = await repo.read(variant)
        assert note is not None, f"{variant!r} failed to resolve"
        assert note.path == "README.md"


async def test_repository_list_paths_decrypts_metadata(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """``list_paths`` surfaces real decrypted paths, not the encrypted blobs."""
    assert vault.salt is not None
    listed = await repo.list_paths()
    paths = {item["path"] for item in listed}
    # No encrypted-blob or obfuscated-id leakage into the listing.
    assert not any(p.startswith(("/\\:", "f:")) for p in paths)
    assert "%=" not in "".join(paths)
    # Every live obfuscated note's real (decrypted) path is present.
    expected = {_decrypt_metadata(doc, vault.salt)["path"] for doc in _live_obfuscated_docs(vault)}
    assert expected <= paths, f"missing {expected - paths}"
    # Metadata really was decrypted: mtimes are populated, not zeroed.
    assert any(item["mtime"] > 0 for item in listed)


async def test_repository_missing_salt_raises_on_property_encrypted(vault: LoadedVault) -> None:
    """Without the salt, a Property-Encrypted read fails loudly (not silently empty)."""
    repo = NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=None,  # salt not loaded
        obfuscate_paths=True,
        case_sensitive=False,
    )
    with pytest.raises(EncryptedVaultError):
        await repo.read("README.md")


# --------------------------------------------------------------------------
# Writes into a Property-Encrypted vault
#
# The write path must reproduce the plugin's on-disk shape: an obfuscated `f:`
# id, the real metadata as an encrypted `/\\:%=` blob in the `path` field,
# zeroed top-level fields, and encrypted (`h:+`) content-addressed chunks.
# --------------------------------------------------------------------------


async def test_repository_write_creates_property_encrypted_doc(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """Creating a note writes the obfuscated id + encrypted metadata blob, and
    the blob decrypts to metadata whose chunk IDs reproduce exactly."""
    assert vault.salt is not None
    content = "# New Note\n\nproperty-encrypted write test with a bit of body text.\n"
    note = await repo.create("zzz-write-test.md", content)
    assert note.path == "zzz-write-test.md"

    f_id = path_to_id(
        "zzz-write-test.md", obfuscate=True, passphrase=PASSPHRASE, case_sensitive=False
    )
    raw = await vault.couch.get(f_id)
    assert raw is not None
    # On-disk shape matches the plugin: obfuscated id, encrypted blob, zeroed fields.
    assert raw["_id"] == f_id
    assert raw["path"].startswith("/\\:") and "%=" in raw["path"]
    assert raw["children"] == []
    assert raw["mtime"] == 0 and raw["ctime"] == 0 and raw["size"] == 0

    # The metadata blob decrypts to the real values, and chunk IDs reproduce.
    meta = _decrypt_metadata(raw, vault.salt)
    assert meta["path"] == "zzz-write-test.md"
    assert meta["size"] == len(content.encode("utf-8"))
    assert meta["children"] == [
        hash_chunk(p, encrypted=True, hashed_passphrase=HASHED_PASSPHRASE)
        for p in split_pieces_rabin_karp(content)
    ]
    assert _children_encrypted(meta)  # h:+ encrypted chunks


async def test_repository_write_read_roundtrip_property_encrypted(repo: NoteRepository) -> None:
    """A note written into the obfuscated vault reads back through the repo."""
    content = "# Round Trip\n\n中文 🚀 unicode survives the encrypted-metadata write.\n"
    await repo.create("round/trip note.md", content)
    back = await repo.read("round/trip note.md")
    assert back is not None
    assert back.path == "round/trip note.md"
    assert back.content == content
    assert back.size == len(content.encode("utf-8"))


async def test_repository_update_preserves_ctime_property_encrypted(repo: NoteRepository) -> None:
    """Updating an obfuscated note bumps mtime but preserves the original ctime."""
    created = await repo.create("update-me.md", "v1\n")
    assert created.ctime > 0
    updated = await repo.update("update-me.md", "v2 content\n")
    assert updated.ctime == created.ctime
    assert updated.mtime >= created.mtime
    back = await repo.read("update-me.md")
    assert back is not None and back.content == "v2 content\n"


async def test_repository_write_requires_salt_property_encrypted(vault: LoadedVault) -> None:
    """Obfuscated writes without the salt fail loudly rather than corrupting."""
    repo = NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=None,
        obfuscate_paths=True,
        case_sensitive=False,
    )
    with pytest.raises(EncryptedVaultError):
        await repo.create("no-salt.md", "body\n")
