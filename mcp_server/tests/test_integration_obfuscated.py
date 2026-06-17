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
(prefixed with ``/\\:`` then an HKDF ``%=`` ciphertext). These tests decrypt
that blob directly. Wiring Property-Encryption awareness into ``NoteRepository``
(so ``read()``/``list_paths()`` work transparently) is tracked separately; see
``test_repository_read_property_encryption_pending``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from obsidian_livesync_mcp.livesync import encryption
from obsidian_livesync_mcp.livesync.chunks import (
    hash_chunk,
    hashed_passphrase,
    split_pieces_rabin_karp,
)
from obsidian_livesync_mcp.livesync.links import LinkGraph
from obsidian_livesync_mcp.livesync.models import extract_frontmatter
from obsidian_livesync_mcp.livesync.notes import _decode_chunk_payload
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
# Pending: NoteRepository transparent read of Property-Encrypted vaults
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="NoteRepository does not yet decode the Property-Encryption metadata "
    "blob; obfuscated reads return empty until that support lands.",
    strict=True,
)
async def test_repository_read_property_encryption_pending(vault: LoadedVault) -> None:
    from typing import cast

    from obsidian_livesync_mcp.couchdb import CouchDBClient
    from obsidian_livesync_mcp.livesync.notes import NoteRepository

    repo = NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=vault.salt,
        obfuscate_paths=True,
        case_sensitive=False,
    )
    note = await repo.read("README.md")
    assert note is not None
    assert "Fixture Vault Documentation" in note.content
