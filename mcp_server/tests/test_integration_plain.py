"""Integration tests against a real exported plain (unencrypted) vault.

Unlike the unit tests, these load a JSON snapshot of an *actual* Obsidian
LiveSync CouchDB database produced by the real plugin (V3 chunk splitter,
case-insensitive path handling) and run the real `NoteRepository` against
it. They validate that our reading / hashing / reassembly / splitting logic
is byte-compatible with genuine plugin output, including case-insensitive
path resolution and full V3 Rabin-Karp chunk-boundary parity.

Fixture: mcp_server/fixtures/plain/  (E2EE off, path obfuscation off)
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

VAULT_NAME = "plain"

# Skip the whole module cleanly if the fixture export isn't present.
try:
    _VAULT = load_vault(VAULT_NAME)
except FileNotFoundError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"plain vault fixture missing: {exc}", allow_module_level=True)


@pytest.fixture
def vault() -> LoadedVault:
    # Re-load per test so the fake CouchDB starts clean (writes mutate it).
    return load_vault(VAULT_NAME)


@pytest.fixture
def repo(vault: LoadedVault) -> NoteRepository:
    return NoteRepository(cast(CouchDBClient, vault.couch))


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


def _soft_deleted_note_ids(vault: LoadedVault) -> list[str]:
    """Note doc IDs flagged as soft-deleted (``deleted: true``).

    The real plugin purges some deletions entirely, so a given export may or
    may not contain a soft-deleted note. Tests look one up dynamically rather
    than assuming a specific hand-built fixture (``soft-deleted-note.md``) is
    present, and skip cleanly when the export has none.
    """
    return [
        doc_id
        for doc_id, doc in vault.raw_docs.items()
        if doc_id.endswith(".md")
        and not doc_id.startswith(("h:", "_"))
        and doc.get("deleted") is True
    ]


# --------------------------------------------------------------------------
# Sanity: the fixture loaded and looks like a real vault
# --------------------------------------------------------------------------


def test_fixture_has_expected_shape(vault: LoadedVault) -> None:
    note_ids = vault.note_ids()
    chunk_ids = vault.chunk_ids()
    assert len(note_ids) >= 6, "expected at least the core hand-built fixtures"
    assert len(chunk_ids) > 100, "expected many chunk docs from the V3 splitter"
    # The stable hand-built fixtures should always be present. (A soft-deleted
    # note is *not* required: the plugin purges some deletions, so it may be
    # absent from a given export; soft-delete behaviour is covered separately
    # and skips when the export contains no deleted note.)
    for expected in (
        "readme.md",
        "note-with-emoji.md",
        "note-with-links.md",
        "linked-note.md",
        "compressible-note.md",
        "multi-chunk-note.md",
    ):
        assert expected in vault.raw_docs, f"missing fixture note {expected!r}"


def test_plain_vault_has_no_salt_but_records_protocol(vault: LoadedVault) -> None:
    # Even with E2EE off, the plugin writes a sync-parameters doc.
    assert vault.protocol_version == 2
    # A salt is present in the doc (unused while E2EE is off); just confirm
    # the loader surfaced it as bytes so encrypted vaults will work the same way.
    assert vault.salt is None or isinstance(vault.salt, bytes)


# --------------------------------------------------------------------------
# Hashing parity — the strongest compatibility signal
# --------------------------------------------------------------------------


def test_every_chunk_id_reproduced_by_hash_chunk(vault: LoadedVault) -> None:
    """hash_chunk(decode(data)) must equal the plugin-assigned chunk _id.

    This proves our XXHash64 + UTF-16-length + base36 hashing matches the
    real plugin for every chunk in the vault.
    """
    mismatches = []
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        raw = _decode_chunk_payload(doc["data"], passphrase=None, pbkdf2_salt=None)
        if hash_chunk(raw) != doc_id:
            mismatches.append(doc_id)
    assert not mismatches, f"{len(mismatches)} chunk IDs did not reproduce: {mismatches[:5]}"


def test_every_chunk_payload_decodes(vault: LoadedVault) -> None:
    """Every chunk's data field decodes without error (compression markers etc.)."""
    for doc_id, doc in vault.raw_docs.items():
        if not doc_id.startswith("h:"):
            continue
        decoded = _decode_chunk_payload(doc["data"], passphrase=None, pbkdf2_salt=None)
        assert isinstance(decoded, str)


# --------------------------------------------------------------------------
# Reading & reassembly (read by stored _id, which resolves in plain mode)
# --------------------------------------------------------------------------


async def test_read_reassembles_readme(repo: NoteRepository) -> None:
    note = await repo.read("readme.md")
    assert note is not None
    assert note.path == "README.md"  # real case preserved in the path field
    assert "Fixture Vault Documentation" in note.content


async def test_multichunk_reassembles_in_order(repo: NoteRepository, vault: LoadedVault) -> None:
    doc = vault.raw_docs["multi-chunk-note.md"]
    assert len(doc["children"]) > 50, "expected a genuinely multi-chunk note"
    note = await repo.read("multi-chunk-note.md")
    assert note is not None
    # Sections must appear in order across chunk boundaries.
    first = note.content.index("Section 001")
    last = note.content.index("Section 120")
    assert first < last, "chunks reassembled out of order"


async def test_emoji_and_cjk_survive_roundtrip(repo: NoteRepository) -> None:
    note = await repo.read("note-with-emoji.md")
    assert note is not None
    # Supplementary-plane emoji and CJK must come back intact (UTF-16 handling).
    assert "🚀" in note.content
    assert "中文" in note.content


async def test_compressible_note_decompresses(repo: NoteRepository) -> None:
    note = await repo.read("compressible-note.md")
    assert note is not None
    assert "Lorem ipsum" in note.content


# --------------------------------------------------------------------------
# Soft-delete handling
# --------------------------------------------------------------------------


async def test_soft_deleted_note_reads_as_none(repo: NoteRepository, vault: LoadedVault) -> None:
    deleted = _soft_deleted_note_ids(vault)
    if not deleted:
        pytest.skip("export contains no soft-deleted note")
    assert await repo.read(deleted[0]) is None


async def test_soft_deleted_excluded_from_listing(repo: NoteRepository, vault: LoadedVault) -> None:
    deleted = _soft_deleted_note_ids(vault)
    if not deleted:
        pytest.skip("export contains no soft-deleted note")
    doc_id = deleted[0]
    listed = {item["path"] for item in await repo.list_paths()}
    # The raw doc still exists and is flagged deleted...
    assert vault.raw_docs[doc_id].get("deleted") is True
    # ...but it must not appear in the note listing (by its stored path).
    real_path = vault.raw_docs[doc_id].get("path", doc_id)
    assert real_path not in listed
    assert doc_id not in listed


async def test_listing_matches_live_notes(repo: NoteRepository, vault: LoadedVault) -> None:
    listed = {item["path"] for item in await repo.list_paths()}
    # No chunk or internal docs leak into the listing.
    assert not any(p.startswith(("h:", "_")) for p in listed)
    # Every live note (by its real `path`) is present.
    for doc_id in _live_note_ids(vault):
        real_path = vault.raw_docs[doc_id].get("path", doc_id)
        assert real_path in listed, f"live note {real_path!r} missing from listing"


# --------------------------------------------------------------------------
# Frontmatter & links
# --------------------------------------------------------------------------


async def test_frontmatter_extracted_from_readme(repo: NoteRepository) -> None:
    note = await repo.read("readme.md")
    assert note is not None
    frontmatter, body = extract_frontmatter(note.content)
    assert frontmatter.get("title") == "Fixture Vault Documentation"
    assert "Fixture Vault Documentation" in body


async def test_link_backfill_produces_backlink(repo: NoteRepository, vault: LoadedVault) -> None:
    """Build the link graph from real note content and check a known edge.

    note-with-links.md contains [[linked-note]], so linked-note should have
    note-with-links.md as a backlink.
    """
    graph = LinkGraph()
    notes: dict[str, str] = {}
    for doc_id in _live_note_ids(vault):
        note = await repo.read(doc_id)
        if note is not None:
            notes[note.path] = note.content
    graph.rebuild_from_notes(notes)

    forward = graph.get_forward_links("note-with-links.md")
    assert "linked-note" in forward, f"forward links were {forward}"
    assert "note-with-links.md" in graph.get_backlinks("linked-note")


# --------------------------------------------------------------------------
# Path edge cases that the "keep the mix" sandbox content gives us
# --------------------------------------------------------------------------


async def test_subfolder_and_spaced_paths_read(repo: NoteRepository, vault: LoadedVault) -> None:
    """Notes in subfolders / with spaces (from the sandbox content) read back."""
    candidates = [doc_id for doc_id in _live_note_ids(vault) if "/" in doc_id or " " in doc_id]
    if not candidates:
        pytest.skip("no subfolder/spaced sandbox notes in this export")
    # Spot-check a handful resolve and assemble without error.
    for doc_id in candidates[:5]:
        note = await repo.read(doc_id)
        assert note is not None, f"failed to read {doc_id!r}"


# --------------------------------------------------------------------------
# Case-insensitive path handling (matches the plugin default; was a gap)
# --------------------------------------------------------------------------


async def test_read_by_truecase_path_resolves(repo: NoteRepository) -> None:
    """The LLM gets "README.md" from list_notes and must be able to read it.

    The plugin stores IDs lowercased by default (case-insensitive), while the
    real-case filename lives in the doc's `path` field. NoteRepository folds
    case when building the lookup ID, so the true-case path resolves and the
    returned note still reports the real-case path.
    """
    note = await repo.read("README.md")
    assert note is not None
    assert note.path == "README.md"
    assert "Fixture Vault Documentation" in note.content


async def test_read_is_case_insensitive_for_arbitrary_casing(repo: NoteRepository) -> None:
    # Any casing of a stored path should resolve to the same note.
    for variant in ("README.MD", "ReadMe.md", "readme.md"):
        note = await repo.read(variant)
        assert note is not None, f"{variant!r} failed to resolve"
        assert note.path == "README.md"


# --------------------------------------------------------------------------
# V3 Rabin-Karp splitter parity (this vault was written by the V3 splitter)
# --------------------------------------------------------------------------


async def test_v3_splitter_reproduces_readme_children(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    doc = vault.raw_docs["readme.md"]
    note = await repo.read("readme.md")
    assert note is not None
    our_children = [hash_chunk(p) for p in split_pieces_rabin_karp(note.content)]
    assert our_children == doc["children"]


async def test_v3_splitter_reproduces_every_note(repo: NoteRepository, vault: LoadedVault) -> None:
    """The V3 port must reproduce the plugin's children for EVERY note.

    This is the strongest write-side compatibility signal: if we re-split the
    assembled content of each note, the resulting chunk IDs must match the
    plugin's exactly, so writes reuse (dedup against) existing chunks.
    """
    failures: list[str] = []
    for doc_id in _live_note_ids(vault):
        doc = vault.raw_docs[doc_id]
        if not doc.get("children"):
            continue
        note = await repo.read(doc_id)
        assert note is not None
        ours = [hash_chunk(p) for p in split_pieces_rabin_karp(note.content)]
        if ours != doc["children"]:
            failures.append(f"{doc_id} (plugin={len(doc['children'])} ours={len(ours)})")
    assert not failures, f"{len(failures)} notes did not reproduce: {failures[:5]}"


async def test_repository_write_produces_v3_chunks(
    repo: NoteRepository, vault: LoadedVault
) -> None:
    """A NoteRepository write (default splitter) reproduces the plugin's chunks.

    Reads the real readme content, writes it to a fresh path, and checks the
    stored parent doc's children match the plugin's chunk IDs for readme — i.e.
    our write path is byte-compatible with the V3 splitter, not just the
    standalone function.
    """
    source = await repo.read("readme.md")
    assert source is not None
    await repo.create("roundtrip-readme.md", source.content)
    written = await vault.couch.get("roundtrip-readme.md")
    assert written is not None
    assert written["children"] == vault.raw_docs["readme.md"]["children"]
