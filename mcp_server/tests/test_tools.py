"""Tests for the MCP tool adapter layer."""

from __future__ import annotations

from typing import cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.errors import NoteNotFoundError
from obsidian_livesync_mcp.livesync.notes import NoteRepository
from obsidian_livesync_mcp.tools.notes import NoteTools
from obsidian_livesync_mcp.tools.search import SearchTools

from .fake_couch import FakeCouchDBClient


@pytest.fixture
def tools() -> NoteTools:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    return NoteTools(repo)


async def test_list_notes_returns_pydantic_model(tools: NoteTools) -> None:
    await tools.create_note("a.md", "x")
    await tools.create_note("b.md", "y")
    out = await tools.list_notes()
    assert out.total == 2
    assert {n.path for n in out.notes} == {"a.md", "b.md"}


async def test_list_notes_respects_limit_and_reports_total(tools: NoteTools) -> None:
    for i in range(5):
        await tools.create_note(f"note{i}.md", "x")
    out = await tools.list_notes(limit=2)
    assert len(out.notes) == 2
    assert out.total == 5


async def test_list_notes_folder_normalizes_trailing_slash(tools: NoteTools) -> None:
    await tools.create_note("projects/a.md", "x")
    await tools.create_note("daily/b.md", "y")
    # No trailing slash — should be normalized
    out = await tools.list_notes(folder="projects")
    assert {n.path for n in out.notes} == {"projects/a.md"}


async def test_read_note_returns_iso_timestamps(tools: NoteTools) -> None:
    await tools.create_note("t.md", "hi")
    out = await tools.read_note("t.md")
    # Pydantic should have converted unix-ms to datetime.
    assert out.content == "hi"
    assert out.ctime.tzinfo is not None
    assert out.mtime.tzinfo is not None


async def test_read_note_missing_raises(tools: NoteTools) -> None:
    with pytest.raises(NoteNotFoundError):
        await tools.read_note("nope.md")


async def test_delete_note_returns_confirmation(tools: NoteTools) -> None:
    await tools.create_note("d.md", "x")
    out = await tools.delete_note("d.md")
    assert out.path == "d.md"
    assert out.deleted is True


async def test_move_note_full_flow(tools: NoteTools) -> None:
    await tools.create_note("old.md", "content")
    moved = await tools.move_note("old.md", "new.md")
    assert moved.path == "new.md"
    with pytest.raises(NoteNotFoundError):
        await tools.read_note("old.md")
    reread = await tools.read_note("new.md")
    assert reread.content == "content"


async def test_semantic_search_returns_empty_with_honest_coverage() -> None:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    tools_n = NoteTools(repo)
    await tools_n.create_note("a.md", "x")
    await tools_n.create_note("b.md", "y")

    search = SearchTools(repo, vectors=None)
    out = await search.semantic_search(query="anything")
    assert out.results == []
    assert out.index_coverage.indexed == 0
    assert out.index_coverage.total == 2
