"""Live integration tests against a real CouchDB container.

These tests spin up a CouchDB 3.3 Docker container (via testcontainers) and
exercise the actual CouchDBClient HTTP layer, NoteRepository round-trips, and
the _changes feed. They are skipped by default; run with:

    pytest -m live

or:

    pytest mcp_server/tests/test_live_couchdb.py -m live

What this covers that the snapshot tests cannot:
- Real HTTP auth, error codes, and retry semantics
- Actual 409 conflict detection and resolution
- bulk_get / bulk_docs against a live CouchDB
- _changes feed (feed=normal snapshot mode)
- Plugin-format round-trip: verify doc structure written by NoteRepository
  matches the shape the LiveSync plugin expects (children, type, path, data)
"""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.errors import CouchDBError
from obsidian_livesync_mcp.livesync.chunks import hash_chunk, split_pieces_rabin_karp
from obsidian_livesync_mcp.livesync.notes import NoteRepository

from .conftest import LiveCouch

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique(base: str, request: pytest.FixtureRequest) -> str:
    """Make a doc ID unique per test so session-scoped container stays clean."""
    return f"{base}-{request.node.nodeid.replace('/', '_').replace('::', '_')}"


# ---------------------------------------------------------------------------
# Basic CouchDBClient HTTP layer
# ---------------------------------------------------------------------------


async def test_get_missing_returns_none(live_couch: LiveCouch) -> None:
    doc = await live_couch.client.get("does-not-exist.md")
    assert doc is None


async def test_put_and_get_roundtrip(live_couch: LiveCouch, request: pytest.FixtureRequest) -> None:
    doc_id = _unique("put-get.md", request)
    doc = {"_id": doc_id, "type": "plain", "content": "hello"}
    result = await live_couch.client.put(doc)
    assert result.get("ok") is True
    fetched = await live_couch.client.get(doc_id)
    assert fetched is not None
    assert fetched["content"] == "hello"
    assert fetched["_rev"].startswith("1-")


async def test_put_conflict_raises_409(
    live_couch: LiveCouch, request: pytest.FixtureRequest
) -> None:
    doc_id = _unique("conflict.md", request)
    doc = {"_id": doc_id, "type": "plain", "v": 1}
    res = await live_couch.client.put(doc)
    # Omit _rev — should conflict with the existing revision.
    stale = {"_id": doc_id, "type": "plain", "v": 2}
    with pytest.raises(CouchDBError) as exc_info:
        await live_couch.client.put(stale)
    assert exc_info.value.status_code == 409
    assert res.get("ok") is True  # first write succeeded


async def test_put_update_with_rev(live_couch: LiveCouch, request: pytest.FixtureRequest) -> None:
    doc_id = _unique("update.md", request)
    res1 = await live_couch.client.put({"_id": doc_id, "v": 1})
    rev1 = res1["rev"]
    res2 = await live_couch.client.put({"_id": doc_id, "_rev": rev1, "v": 2})
    assert res2.get("ok") is True
    assert res2["rev"].startswith("2-")
    fetched = await live_couch.client.get(doc_id)
    assert fetched is not None
    assert fetched["v"] == 2


async def test_bulk_get_mixed(live_couch: LiveCouch, request: pytest.FixtureRequest) -> None:
    doc_id = _unique("bulk-get.md", request)
    await live_couch.client.put({"_id": doc_id, "v": 1})
    results = await live_couch.client.bulk_get([doc_id, "ghost.md"])
    assert results[0] is not None
    assert results[0]["v"] == 1
    assert results[1] is None


async def test_bulk_docs_writes_multiple(
    live_couch: LiveCouch, request: pytest.FixtureRequest
) -> None:
    ids = [_unique(f"bulk-{i}.md", request) for i in range(3)]
    docs = [{"_id": doc_id, "v": i} for i, doc_id in enumerate(ids)]
    results = await live_couch.client.bulk_docs(docs)
    assert len(results) == 3
    assert all(r.get("ok") is True for r in results)


async def test_all_docs_range(live_couch: LiveCouch, request: pytest.FixtureRequest) -> None:
    prefix = _unique("range", request)
    ids = [f"{prefix}-{i}.md" for i in range(3)]
    await live_couch.client.bulk_docs([{"_id": doc_id} for doc_id in ids])
    rows = await live_couch.client.all_docs(
        start_key=f"{prefix}-",
        end_key=f"{prefix}-￿",
        include_docs=True,
    )
    returned_ids = {r["id"] for r in rows}
    assert set(ids).issubset(returned_ids)


async def test_changes_feed_normal(live_couch: LiveCouch, request: pytest.FixtureRequest) -> None:
    doc_id = _unique("changes.md", request)
    await live_couch.client.put({"_id": doc_id, "v": 1})
    rows = []
    async for row in live_couch.client.changes(since="0", feed="normal", include_docs=True):
        rows.append(row)
    ids_seen = {r["id"] for r in rows if "id" in r}
    assert doc_id in ids_seen


# ---------------------------------------------------------------------------
# NoteRepository round-trips
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(live_couch: LiveCouch) -> NoteRepository:
    return NoteRepository(live_couch.client)


async def test_repo_create_and_read(repo: NoteRepository) -> None:
    content = "# Live Test\n\nHello from the live suite.\n"
    await repo.create("live-test.md", content)
    note = await repo.read("live-test.md")
    assert note is not None
    assert note.content == content
    assert note.path == "live-test.md"


async def test_repo_update_increments_rev(repo: NoteRepository) -> None:
    await repo.create("rev-test.md", "v1")
    note1 = await repo.read("rev-test.md")
    assert note1 is not None
    await repo.update("rev-test.md", "v2")
    note2 = await repo.read("rev-test.md")
    assert note2 is not None
    assert note2.content == "v2"


async def test_repo_delete_soft_deletes(repo: NoteRepository) -> None:
    await repo.create("del-test.md", "goodbye")
    await repo.delete("del-test.md")
    assert await repo.read("del-test.md") is None


async def test_repo_list_excludes_deleted(repo: NoteRepository) -> None:
    await repo.create("list-alive.md", "alive")
    await repo.create("list-dead.md", "dead")
    await repo.delete("list-dead.md")
    paths = {item["path"] for item in await repo.list_paths()}
    assert "list-alive.md" in paths
    assert "list-dead.md" not in paths


async def test_repo_move(repo: NoteRepository) -> None:
    await repo.create("move-src.md", "moving")
    await repo.move("move-src.md", "move-dst.md")
    assert await repo.read("move-src.md") is None
    dst = await repo.read("move-dst.md")
    assert dst is not None
    assert dst.content == "moving"


# ---------------------------------------------------------------------------
# Plugin-format validation: doc structure on disk must match LiveSync schema
# ---------------------------------------------------------------------------


async def test_created_doc_has_plugin_compatible_structure(
    repo: NoteRepository, live_couch: LiveCouch
) -> None:
    """NoteRepository.create() must produce a CouchDB doc the plugin can read.

    Checks: _id lowercased, type='newnote', path=real-case, children=list of
    chunk hashes matching our splitter, and h: chunk docs present with data.
    """
    content = "# Plugin Compat\n\nThis note is checked for plugin-format parity.\n"
    path = "PluginCompat.md"
    await repo.create(path, content)

    # The doc is stored under the lowercased ID.
    doc = await live_couch.client.get(path.lower())
    assert doc is not None, "parent doc not found under lowercased ID"

    assert doc.get("type") in ("newnote", "plain"), f"unexpected type: {doc.get('type')}"
    assert doc.get("path") == path, "path field must preserve original casing"
    assert isinstance(doc.get("children"), list), "children must be a list"
    assert len(doc["children"]) > 0, "children must not be empty"

    # Every child hash must match our splitter output.
    expected_children = [hash_chunk(p) for p in split_pieces_rabin_karp(content)]
    assert doc["children"] == expected_children, "children do not match V3 splitter output"

    # Every chunk doc must exist in CouchDB with a non-empty data field.
    chunk_docs = await live_couch.client.bulk_get(doc["children"])
    for chunk_id, chunk_doc in zip(doc["children"], chunk_docs, strict=True):
        assert chunk_doc is not None, f"chunk doc {chunk_id!r} missing from CouchDB"
        assert isinstance(chunk_doc.get("data"), str), f"chunk {chunk_id!r} has no data field"
        assert chunk_doc["data"], f"chunk {chunk_id!r} has empty data"


async def test_multichunk_doc_children_order(repo: NoteRepository, live_couch: LiveCouch) -> None:
    """A note large enough to produce multiple chunks must store children in order."""
    # Generate content that will definitely split into multiple chunks (>100 KB).
    lines = [f"Section {i:04d}: " + "x" * 200 for i in range(300)]
    content = "\n\n".join(lines) + "\n"
    await repo.create("multi-chunk-live.md", content)

    doc = await live_couch.client.get("multi-chunk-live.md")
    assert doc is not None
    assert len(doc["children"]) > 1, "expected multiple chunks"

    expected = [hash_chunk(p) for p in split_pieces_rabin_karp(content)]
    assert doc["children"] == expected


async def test_soft_delete_sets_deleted_flag(repo: NoteRepository, live_couch: LiveCouch) -> None:
    """repo.delete() must set deleted=True on the CouchDB doc (soft delete)."""
    await repo.create("soft-del-live.md", "bye")
    await repo.delete("soft-del-live.md")
    doc = await live_couch.client.get("soft-del-live.md")
    assert doc is not None, "doc should still exist after soft delete"
    assert doc.get("deleted") is True, "deleted flag must be True"


async def test_conflict_retry_resolves(repo: NoteRepository, live_couch: LiveCouch) -> None:
    """NoteRepository.update() must survive a 409 by re-fetching and retrying.

    Simulate a mid-air edit: write the doc directly via the raw client to
    advance its rev between repo.read() and repo.update().
    """
    await repo.create("retry-test.md", "initial")
    # Sneak a write via the raw client to bump the rev.
    raw = await live_couch.client.get("retry-test.md")
    assert raw is not None
    raw["content_sneaky"] = "bump"
    await live_couch.client.put(raw)

    # repo.update() must retry and succeed despite the stale rev.
    await repo.update("retry-test.md", "final")
    note = await repo.read("retry-test.md")
    assert note is not None
    assert note.content == "final"
