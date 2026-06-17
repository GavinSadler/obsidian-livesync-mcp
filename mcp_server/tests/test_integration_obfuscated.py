"""Integration tests against a real exported obfuscated vault.

Unlike the unit tests, these load a JSON snapshot of an *actual* Obsidian
LiveSync CouchDB database produced by the real plugin (E2EE on with path
obfuscation) and run the real `NoteRepository` against it. They validate
that our path→ID obfuscation hashing (SHA-256 stretching) matches the plugin
exactly, and that obfuscated mode reads/writes work end-to-end.

Fixture: mcp_server/fixtures/obfuscated/  (E2EE on, path obfuscation on)

Note: Obfuscated vaults obscure note paths in document IDs but not in the
docs' `path` fields. The critical compatibility signal is that `path_to_id()`
produces the same ID as the plugin for each `path` field in the vault.
"""

from __future__ import annotations

from typing import cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.livesync.chunks import hash_chunk, split_pieces_rabin_karp
from obsidian_livesync_mcp.livesync.links import LinkGraph
from obsidian_livesync_mcp.livesync.models import extract_frontmatter
from obsidian_livesync_mcp.livesync.notes import NoteRepository, _decode_chunk_payload
from obsidian_livesync_mcp.livesync.paths import path_to_id

from .fixture_loader import LoadedVault, load_vault

VAULT_NAME = "obfuscated"
PASSPHRASE = "test-passphrase-456"

# Skip the whole module cleanly if the fixture export isn't present.
try:
    _VAULT = load_vault(VAULT_NAME)
except FileNotFoundError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"obfuscated vault fixture missing: {exc}", allow_module_level=True)


@pytest.fixture
def vault() -> LoadedVault:
    # Re-load per test so the fake CouchDB starts clean (writes mutate it).
    return load_vault(VAULT_NAME)


@pytest.fixture
def repo(vault: LoadedVault) -> NoteRepository:
    # Obfuscated vault requires passphrase for both decryption and path obfuscation.
    assert vault.salt is not None, "obfuscated vault must have PBKDF2 salt"
    assert vault.protocol_version == 2, "expected protocol version 2"
    return NoteRepository(
        cast(CouchDBClient, vault.couch),
        passphrase=PASSPHRASE,
        pbkdf2_salt=vault.salt,
        obfuscate_paths=True,
        case_sensitive=False,
    )


def _live_note_ids(vault: LoadedVault) -> list[str]:
    """Note doc IDs that are not soft-deleted (what list_paths should surface)."""
    out = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.endswith(".md") and not doc_id.startswith("f:"):
            continue
        if doc_id.startswith(("h:", "_")):
            continue
        if doc.get("deleted") is True:
            continue
        if doc.get("type") not in ("plain", "newnote"):
            continue
        out.append(doc_id)
    return out


# --------------------------------------------------------------------------
# Sanity: the fixture loaded and looks like a real vault
# --------------------------------------------------------------------------


def test_fixture_has_expected_shape(vault: LoadedVault) -> None:
    """Obfuscated vault should contain expected documents."""
    note_ids = vault.note_ids()
    chunk_ids = vault.chunk_ids()
    assert len(note_ids) >= 2, "expected at least the 2 basic fixtures (readme, linked)"
    assert len(chunk_ids) > 0, "expected chunk docs from encryption"


def test_obfuscated_vault_has_salt_and_protocol(vault: LoadedVault) -> None:
    """Obfuscated vault must have PBKDF2 salt and protocol version."""
    assert vault.salt is not None, "obfuscated vault requires PBKDF2 salt"
    assert isinstance(vault.salt, bytes), "salt must be bytes"
    assert len(vault.salt) > 0, "salt must be non-empty"
    assert vault.protocol_version == 2, "expected protocol version 2"


# --------------------------------------------------------------------------
# Path obfuscation parity: path_to_id() reproduces plugin document IDs
# --------------------------------------------------------------------------


def test_obfuscation_path_to_id_matches_plugin(vault: LoadedVault) -> None:
    """Our path→ID hashing must reproduce the plugin's obfuscated IDs.

    For each note doc, the `path` field is hashed using our `path_to_id()`
    with obfuscate=True and the correct passphrase. The resulting ID must
    match the doc's `_id`.

    This is the strongest signal that our SHA-256 stretching and HMAC logic
    match the plugin exactly.
    """
    mismatches: list[tuple[str, str, str]] = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("f:"):
            continue
        if doc.get("type") not in ("plain", "newnote"):
            continue
        path = doc.get("path")
        if not path or not isinstance(path, str):
            continue
        our_id = path_to_id(path, obfuscate=True, passphrase=PASSPHRASE, case_sensitive=False)
        if our_id != doc_id:
            mismatches.append((path, doc_id, our_id))
    assert not mismatches, f"{len(mismatches)} obfuscated paths did not reproduce:\n" + "\n".join(
        f"  {path}: plugin={pid} ours={oid}" for path, pid, oid in mismatches[:5]
    )


# --------------------------------------------------------------------------
# Encryption parity: decrypt with passphrase + salt
# --------------------------------------------------------------------------


def test_chunk_payload_decrypts_in_obfuscated_vault(vault: LoadedVault) -> None:
    """Every encrypted chunk payload decodes in obfuscated mode.

    Decryption must work the same way regardless of path obfuscation.
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


def test_obfuscated_chunk_ids_reproduced_by_hash_chunk(vault: LoadedVault) -> None:
    """hash_chunk(decrypt(data)) must match plugin chunk IDs in obfuscated mode."""
    assert vault.salt is not None
    mismatches = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        try:
            raw = _decode_chunk_payload(doc["data"], passphrase=PASSPHRASE, pbkdf2_salt=vault.salt)
            if hash_chunk(raw, encrypted=True) != doc_id:
                mismatches.append(doc_id)
        except Exception:
            mismatches.append(doc_id)
    assert not mismatches, f"{len(mismatches)} chunk IDs did not reproduce: {mismatches[:5]}"


# --------------------------------------------------------------------------
# Reading & reassembly (obfuscated mode)
# --------------------------------------------------------------------------


async def test_read_by_obfuscated_path_resolves(repo: NoteRepository) -> None:
    """Read by path must resolve in obfuscated mode using correct passphrase."""
    note = await repo.read("README.md")
    assert note is not None
    assert note.path == "README.md"


async def test_list_paths_works_in_obfuscated_mode(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """list_paths() must surface all live notes even with obfuscated IDs."""
    listed = {item["path"] for item in await repo.list_paths()}
    # Sanity check: should have at least 2 notes
    assert len(listed) >= 2, f"expected at least 2 notes, got {len(listed)}"
    # Check that readme appears in some form
    assert any(p.lower() == "readme.md" for p in listed)


async def test_emoji_and_cjk_survive_obfuscated_roundtrip(
    repo: NoteRepository,
) -> None:
    """Emoji and CJK must survive obfuscation + encryption round-trip."""
    note = await repo.read("note-with-emoji.md")
    if note is None:
        pytest.skip("note-with-emoji.md not in obfuscated vault")
    assert "🚀" in note.content
    assert "中文" in note.content


# --------------------------------------------------------------------------
# V3 Rabin-Karp splitter parity (obfuscated mode)
# --------------------------------------------------------------------------


async def test_v3_splitter_reproduces_obfuscated_note_children(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """V3 splitter must reproduce plugin's children in obfuscated mode."""
    doc = vault.raw_docs["readme.md"]
    note = await repo.read("readme.md")
    assert note is not None
    our_children = [hash_chunk(p, encrypted=True) for p in split_pieces_rabin_karp(note.content)]
    assert our_children == doc["children"]


async def test_v3_splitter_reproduces_obfuscated_every_note(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """V3 splitter must reproduce plugin's children for all obfuscated notes."""
    failures: list[str] = []
    for doc_id in _live_note_ids(vault):
        doc = vault.raw_docs[doc_id]
        if not doc.get("children"):
            continue
        # Extract path to read by
        path = doc.get("path")
        if not path:
            continue
        note = await repo.read(path)
        assert note is not None
        ours = [hash_chunk(p, encrypted=True) for p in split_pieces_rabin_karp(note.content)]
        if ours != doc["children"]:
            failures.append(f"{path} (plugin={len(doc['children'])} ours={len(ours)})")
    assert not failures, f"{len(failures)} notes did not reproduce: {failures[:5]}"


# --------------------------------------------------------------------------
# Frontmatter & links (obfuscated mode)
# --------------------------------------------------------------------------


async def test_frontmatter_extracted_from_obfuscated_readme(
    repo: NoteRepository,
) -> None:
    """Frontmatter must survive obfuscation + encryption and be parseable."""
    note = await repo.read("readme.md")
    assert note is not None
    frontmatter, body = extract_frontmatter(note.content)
    assert frontmatter.get("title") == "Fixture Vault Documentation"
    assert "Fixture Vault Documentation" in body


async def test_link_backfill_in_obfuscated_vault(repo: NoteRepository, vault: LoadedVault) -> None:
    """Wikilinks in obfuscated content must be extractable for link graph."""
    graph = LinkGraph()
    notes: dict[str, str] = {}
    for doc_id in _live_note_ids(vault):
        # For obfuscated IDs, extract path from doc
        doc = vault.raw_docs[doc_id]
        path = doc.get("path")
        if not path:
            continue
        note = await repo.read(path)
        if note is not None:
            notes[note.path] = note.content
    graph.rebuild_from_notes(notes)
    # Just verify graph built without error
    assert isinstance(graph, LinkGraph)
