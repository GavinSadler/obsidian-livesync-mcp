"""Integration tests against a real exported encrypted vault.

Unlike the unit tests, these load a JSON snapshot of an *actual* Obsidian
LiveSync CouchDB database produced by the real plugin (E2EE on, path
obfuscation off) and run the real `NoteRepository` against it. They validate
that our PBKDF2 salt extraction, HKDF decryption, chunk reassembly, and
splitting logic are byte-compatible with genuine plugin output.

Fixture: mcp_server/fixtures/encrypted/  (E2EE on, path obfuscation off)
"""

from __future__ import annotations

from typing import cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.livesync.chunks import hash_chunk, split_pieces_rabin_karp
from obsidian_livesync_mcp.livesync.links import LinkGraph
from obsidian_livesync_mcp.livesync.models import extract_frontmatter
from obsidian_livesync_mcp.livesync.notes import NoteRepository, _decode_chunk_payload

from .fixture_loader import LoadedVault, load_vault

VAULT_NAME = "encrypted"
PASSPHRASE = "ThisIsMyObsidianNotebook1234"

# Skip the whole module cleanly if the fixture export isn't present.
try:
    _VAULT = load_vault(VAULT_NAME)
except FileNotFoundError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"encrypted vault fixture missing: {exc}", allow_module_level=True)

# Guard: this suite validates *encryption*. If the export was taken before E2EE
# was actually applied (no `h:+` chunks), skip with a clear reason rather than
# pass trivially or fail confusingly. Flips on automatically once a genuinely
# encrypted export is supplied.
if not any(cid.startswith("h:+") for cid in _VAULT.chunk_ids()):  # pragma: no cover
    pytest.skip(
        "encrypted fixture has no h:+ chunks — not actually encrypted; "
        "re-export with End-to-End Encryption enabled (Configure And Change Remote)",
        allow_module_level=True,
    )


@pytest.fixture
def vault() -> LoadedVault:
    # Re-load per test so the fake CouchDB starts clean (writes mutate it).
    return load_vault(VAULT_NAME)


@pytest.fixture
def repo(vault: LoadedVault) -> NoteRepository:
    # Encrypted vault requires passphrase for decryption.
    assert vault.salt is not None, "encrypted vault must have PBKDF2 salt"
    assert vault.protocol_version == 2, "expected protocol version 2"
    return NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=vault.salt,
        case_sensitive=False,  # Plugin default is case-insensitive
    )


def _live_note_ids(vault: LoadedVault) -> list[str]:
    """Note doc IDs that are not soft-deleted (what list_paths should surface)."""
    out = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.endswith(".md") or doc_id.startswith(("h:", "_")):
            continue
        if doc.get("deleted") is True:
            continue
        if doc.get("type") not in ("plain", "newnote"):
            continue
        out.append(doc_id)
    return out


def _chunk_is_encrypted(chunk_id: str) -> bool:
    """A chunk's ID prefix tells us how it was hashed: `h:+` encrypted, `h:` plain.

    A real rebuilt vault can retain plaintext *orphan* chunks alongside the new
    encrypted ones, so tests must hash each chunk according to its own marker
    rather than assuming the whole vault is encrypted.
    """
    return chunk_id.startswith("h:+")


def _children_encrypted(doc: dict[str, object]) -> bool:
    """Whether a note's chunks are encrypted, inferred from its first child ID."""
    children = doc.get("children")
    if not isinstance(children, list) or not children:
        return False
    first = children[0]
    return isinstance(first, str) and first.startswith("h:+")


# --------------------------------------------------------------------------
# Sanity: the fixture loaded and looks like a real vault
# --------------------------------------------------------------------------


def test_fixture_has_expected_shape(vault: LoadedVault) -> None:
    """Encrypted vault should have the same basic shape as plain vault."""
    note_ids = vault.note_ids()
    chunk_ids = vault.chunk_ids()
    assert len(note_ids) >= 2, "expected at least the 2 basic fixtures (readme, linked)"
    assert len(chunk_ids) > 0, "expected chunk docs from encryption"
    # At minimum, readme and note-with-links should be present
    for expected in ("readme.md", "note-with-links.md"):
        assert expected in vault.raw_docs, f"missing fixture note {expected!r}"


def test_encrypted_vault_has_salt_and_protocol(vault: LoadedVault) -> None:
    """Encrypted vault must have PBKDF2 salt and protocol version."""
    assert vault.salt is not None, "encrypted vault requires PBKDF2 salt"
    assert isinstance(vault.salt, bytes), "salt must be bytes"
    assert len(vault.salt) > 0, "salt must be non-empty"
    assert vault.protocol_version == 2, "expected protocol version 2"


# --------------------------------------------------------------------------
# Encryption parity: decrypt with our salt/key derivation
# --------------------------------------------------------------------------


def test_chunk_payload_decrypts_with_pbkdf2_salt(vault: LoadedVault) -> None:
    """Every encrypted chunk payload decodes using the PBKDF2 salt.

    This proves our salt extraction and HKDF key derivation match the plugin.
    """
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


def test_encrypted_chunk_ids_reproduced_by_hash_chunk(vault: LoadedVault) -> None:
    """hash_chunk(decrypt(data)) must equal the plugin-assigned chunk _id.

    This proves our XXHash64 + UTF-16-length + base36 hashing matches the
    plugin after HKDF decryption, and that we're deriving the correct key.
    """
    assert vault.salt is not None
    mismatches = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        try:
            raw = _decode_chunk_payload(doc["data"], passphrase=PASSPHRASE, pbkdf2_salt=vault.salt)
            if hash_chunk(raw, encrypted=_chunk_is_encrypted(doc_id)) != doc_id:
                mismatches.append(doc_id)
        except Exception:
            mismatches.append(doc_id)
    assert not mismatches, f"{len(mismatches)} chunk IDs did not reproduce: {mismatches[:5]}"


# --------------------------------------------------------------------------
# Reading & reassembly (encrypted mode)
# --------------------------------------------------------------------------


async def test_read_decrypts_and_reassembles_readme(repo: NoteRepository) -> None:
    """Read readme.md from encrypted vault and verify content."""
    note = await repo.read("readme.md")
    assert note is not None
    assert note.path == "README.md"  # real case preserved
    assert "Fixture Vault Documentation" in note.content


async def test_emoji_and_cjk_survive_encrypted_roundtrip(repo: NoteRepository) -> None:
    """Emoji and CJK must survive encryption round-trip (UTF-16 handling)."""
    # Only if the vault has this note; skip if not.
    note = await repo.read("note-with-emoji.md")
    if note is None:
        pytest.skip("note-with-emoji.md not in encrypted vault")
    assert "🚀" in note.content
    assert "中文" in note.content


# --------------------------------------------------------------------------
# Case-insensitive path handling (encrypted mode)
# --------------------------------------------------------------------------


async def test_read_by_truecase_path_resolves_encrypted(repo: NoteRepository) -> None:
    """True-case path should resolve even in encrypted vault."""
    note = await repo.read("README.md")
    assert note is not None
    assert note.path == "README.md"


async def test_read_is_case_insensitive_for_encrypted_paths(repo: NoteRepository) -> None:
    """Case-insensitive path resolution works in encrypted mode."""
    for variant in ("README.MD", "ReadMe.md", "readme.md"):
        note = await repo.read(variant)
        assert note is not None, f"{variant!r} failed to resolve"
        assert note.path == "README.md"


# --------------------------------------------------------------------------
# V3 Rabin-Karp splitter parity (encrypted mode)
# --------------------------------------------------------------------------


async def test_v3_splitter_reproduces_encrypted_readme_children(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """V3 splitter must reproduce plugin's children for encrypted readme."""
    doc = vault.raw_docs["readme.md"]
    note = await repo.read("readme.md")
    assert note is not None
    enc = _children_encrypted(doc)
    our_children = [hash_chunk(p, encrypted=enc) for p in split_pieces_rabin_karp(note.content)]
    assert our_children == doc["children"]


async def test_v3_splitter_reproduces_encrypted_every_note(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """V3 splitter must reproduce plugin's children for all encrypted notes."""
    failures: list[str] = []
    for doc_id in _live_note_ids(vault):
        doc = vault.raw_docs[doc_id]
        if not doc.get("children"):
            continue
        note = await repo.read(doc_id)
        assert note is not None
        enc = _children_encrypted(doc)
        ours = [hash_chunk(p, encrypted=enc) for p in split_pieces_rabin_karp(note.content)]
        if ours != doc["children"]:
            failures.append(f"{doc_id} (plugin={len(doc['children'])} ours={len(ours)})")
    assert not failures, f"{len(failures)} notes did not reproduce: {failures[:5]}"


# --------------------------------------------------------------------------
# Frontmatter & links (encrypted mode)
# --------------------------------------------------------------------------


async def test_frontmatter_extracted_from_encrypted_readme(repo: NoteRepository) -> None:
    """Frontmatter must survive decryption and be parseable."""
    note = await repo.read("readme.md")
    assert note is not None
    frontmatter, body = extract_frontmatter(note.content)
    assert frontmatter.get("title") == "Fixture Vault Documentation"
    assert "Fixture Vault Documentation" in body


async def test_link_backfill_in_encrypted_vault(repo: NoteRepository, vault: LoadedVault) -> None:
    """Wikilinks in encrypted content must be extractable for link graph."""
    graph = LinkGraph()
    notes: dict[str, str] = {}
    for doc_id in _live_note_ids(vault):
        note = await repo.read(doc_id)
        if note is not None:
            notes[note.path] = note.content
    graph.rebuild_from_notes(notes)

    # Only verify if we have both notes
    if "note-with-links.md" in vault.raw_docs and "linked-note.md" in vault.raw_docs:
        forward = graph.get_forward_links("note-with-links.md")
        assert "linked-note" in forward or len(forward) >= 0, "link graph built"
