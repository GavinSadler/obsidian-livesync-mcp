"""Tests for new MCP tools: batch read, append, keyword search, links, history."""

from __future__ import annotations

from typing import cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.errors import NoteNotFoundError
from obsidian_livesync_mcp.livesync.links import LinkGraph
from obsidian_livesync_mcp.livesync.notes import NoteRepository
from obsidian_livesync_mcp.tools.history import HistoryTools
from obsidian_livesync_mcp.tools.links import LinkTools
from obsidian_livesync_mcp.tools.notes import NoteTools
from obsidian_livesync_mcp.tools.search import SearchTools

from .fake_couch import FakeCouchDBClient


@pytest.fixture
def note_tools() -> NoteTools:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    return NoteTools(repo)


@pytest.fixture
def search_tools() -> tuple[SearchTools, NoteTools]:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    notes = NoteTools(repo)
    search = SearchTools(repo, vectors=None)
    return search, notes


@pytest.fixture
def link_tools() -> tuple[LinkTools, NoteTools, LinkGraph]:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    notes = NoteTools(repo)
    graph = LinkGraph()
    links = LinkTools(graph, repo)
    return links, notes, graph


@pytest.fixture
def history_tools() -> tuple[HistoryTools, NoteTools]:
    fake = FakeCouchDBClient()
    repo = NoteRepository(cast(CouchDBClient, fake))
    notes = NoteTools(repo)
    history = HistoryTools(cast(CouchDBClient, fake))
    return history, notes


# --- Batch Read Tests ---


async def test_read_notes_batch_all_succeed(note_tools: NoteTools) -> None:
    await note_tools.create_note("a.md", "alpha")
    await note_tools.create_note("b.md", "beta")
    await note_tools.create_note("c.md", "gamma")

    out = await note_tools.read_notes(["a.md", "b.md", "c.md"])
    assert out.succeeded == 3
    assert out.failed == 0
    assert "a.md" in out.notes
    assert "b.md" in out.notes
    assert "c.md" in out.notes


async def test_read_notes_batch_some_missing(note_tools: NoteTools) -> None:
    await note_tools.create_note("a.md", "alpha")

    out = await note_tools.read_notes(["a.md", "missing.md"])
    assert out.succeeded == 1
    assert out.failed == 1


async def test_read_notes_batch_empty(note_tools: NoteTools) -> None:
    out = await note_tools.read_notes([])
    assert out.succeeded == 0
    assert out.failed == 0
    assert out.notes == {}


# --- Append Note Tests ---


async def test_append_note_basic(note_tools: NoteTools) -> None:
    await note_tools.create_note("log.md", "Line 1")
    out = await note_tools.append_note("log.md", "Line 2")
    assert "Line 1" in out.content
    assert "Line 2" in out.content
    assert out.appended_bytes > 0


async def test_append_note_missing_raises(note_tools: NoteTools) -> None:
    with pytest.raises(NoteNotFoundError):
        await note_tools.append_note("nope.md", "content")


async def test_append_note_custom_separator(note_tools: NoteTools) -> None:
    await note_tools.create_note("log.md", "Line 1")
    out = await note_tools.append_note("log.md", "Line 2", separator="\n\n")
    assert out.content == "Line 1\n\nLine 2"


async def test_append_note_preserves_existing_content(note_tools: NoteTools) -> None:
    await note_tools.create_note("doc.md", "existing content")
    out = await note_tools.append_note("doc.md", "more")
    assert out.content.startswith("existing content")


# --- Frontmatter Reading Tests ---


async def test_read_note_extracts_frontmatter(note_tools: NoteTools) -> None:
    content = """---
title: My Note
tags: [foo, bar]
---
Body text here."""
    await note_tools.create_note("note.md", content)
    out = await note_tools.read_note("note.md")
    assert out.frontmatter is not None
    assert out.frontmatter.get("title") == "My Note"
    assert out.tags is not None
    assert set(out.tags) == {"foo", "bar"}
    assert out.content.strip() == "Body text here."


async def test_read_note_no_frontmatter(note_tools: NoteTools) -> None:
    await note_tools.create_note("note.md", "Just plain content.")
    out = await note_tools.read_note("note.md")
    assert out.frontmatter is None
    assert out.tags is None


# --- Keyword Search Tests ---


async def test_keyword_search_simple_match(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "Line 1\nHello World\nLine 3")
    await notes.create_note("b.md", "No match here")

    out = await search.keyword_search("Hello")
    assert out.total_matches == 1
    assert len(out.matches) == 1
    assert out.matches[0].path == "a.md"
    assert out.matches[0].line_number == 2
    assert "Hello World" in out.matches[0].snippet


async def test_keyword_search_case_insensitive(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "HELLO world")

    out = await search.keyword_search("hello", case_sensitive=False)
    assert out.total_matches == 1


async def test_keyword_search_case_sensitive(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "HELLO world")

    out = await search.keyword_search("hello", case_sensitive=True)
    assert out.total_matches == 0


async def test_keyword_search_multiple_files(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "alpha test")
    await notes.create_note("b.md", "beta test")
    await notes.create_note("c.md", "gamma")

    out = await search.keyword_search("test")
    assert out.total_matches == 2


async def test_keyword_search_path_prefix(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("projects/a.md", "test here")
    await notes.create_note("daily/b.md", "test there")

    out = await search.keyword_search("test", path_prefix="projects/")
    assert out.total_matches == 1
    assert out.matches[0].path == "projects/a.md"


async def test_keyword_search_regex(search_tools: tuple[SearchTools, NoteTools]) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "TODO: fix bug\nNOTE: see ref\nTODO: implement")

    out = await search.keyword_search("^TODO:", regex=True)
    assert out.total_matches == 2


async def test_keyword_search_invalid_regex(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    from obsidian_livesync_mcp.errors import LiveSyncMCPError

    search, notes = search_tools
    await notes.create_note("a.md", "content")

    with pytest.raises(LiveSyncMCPError):
        await search.keyword_search("[invalid", regex=True)


async def test_keyword_search_empty_pattern(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    await notes.create_note("a.md", "content")

    out = await search.keyword_search("")
    assert out.total_matches == 0
    assert out.matches == []


async def test_keyword_search_limit(
    search_tools: tuple[SearchTools, NoteTools],
) -> None:
    search, notes = search_tools
    content = "\n".join(f"line {i} match" for i in range(50))
    await notes.create_note("big.md", content)

    out = await search.keyword_search("match", limit=10)
    assert len(out.matches) == 10
    assert out.total_matches == 50


# --- Link Tools Tests ---


async def test_get_forward_links(
    link_tools: tuple[LinkTools, NoteTools, LinkGraph],
) -> None:
    links, _, graph = link_tools
    graph.add_or_update("source.md", "Links to [[target1]] and [[target2]].")

    out = await links.get_forward_links("source.md")
    assert out.path == "source.md"
    assert set(out.forward_links) == {"target1", "target2"}
    assert out.backlinks == []


async def test_get_backlinks(
    link_tools: tuple[LinkTools, NoteTools, LinkGraph],
) -> None:
    links, _, graph = link_tools
    graph.add_or_update("a.md", "[[target]]")
    graph.add_or_update("b.md", "[[target]]")

    out = await links.get_backlinks("target")
    assert out.path == "target"
    assert set(out.backlinks) == {"a.md", "b.md"}
    assert out.forward_links == []


async def test_get_links_both_directions(
    link_tools: tuple[LinkTools, NoteTools, LinkGraph],
) -> None:
    links, _, graph = link_tools
    graph.add_or_update("hub.md", "[[a]] and [[b]]")
    graph.add_or_update("inbound.md", "[[hub]]")

    out = await links.get_links("hub.md")
    assert set(out.forward_links) == {"a", "b"}


async def test_get_forward_links_missing_path(
    link_tools: tuple[LinkTools, NoteTools, LinkGraph],
) -> None:
    links, _, _ = link_tools
    out = await links.get_forward_links("missing.md")
    assert out.forward_links == []


async def test_get_backlinks_missing_path(
    link_tools: tuple[LinkTools, NoteTools, LinkGraph],
) -> None:
    links, _, _ = link_tools
    out = await links.get_backlinks("missing.md")
    assert out.backlinks == []


# --- History Tools Tests ---


async def test_recent_changes_returns_recent(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    await notes.create_note("a.md", "content")
    await notes.create_note("b.md", "content")

    out = await history.recent_changes()
    paths = [c.path for c in out.changes]
    assert "a.md" in paths or "b.md" in paths


async def test_recent_changes_with_since_seq(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    await notes.create_note("a.md", "content")

    out1 = await history.recent_changes()
    watermark = out1.watermark_seq

    await notes.create_note("b.md", "more")
    out2 = await history.recent_changes(since_seq=watermark)
    paths = [c.path for c in out2.changes]
    assert "b.md" in paths


async def test_recent_changes_path_prefix_filter(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    await notes.create_note("projects/foo.md", "x")
    await notes.create_note("daily/bar.md", "y")

    out = await history.recent_changes(path_prefix="projects/")
    for change in out.changes:
        assert change.path.startswith("projects/")


async def test_recent_changes_limit(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    for i in range(10):
        await notes.create_note(f"note{i}.md", "x")

    out = await history.recent_changes(limit=3)
    assert len(out.changes) <= 3


async def test_recent_changes_includes_watermark(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    await notes.create_note("a.md", "x")

    out = await history.recent_changes()
    assert out.watermark_seq > 0


async def test_recent_changes_deleted_notes(
    history_tools: tuple[HistoryTools, NoteTools],
) -> None:
    history, notes = history_tools
    await notes.create_note("a.md", "x")
    await notes.delete_note("a.md")

    out_with = await history.recent_changes(include_deleted=True)
    out_without = await history.recent_changes(include_deleted=False)
    # The deleted note should appear in `with` but not `without`
    deleted_paths_with = [c.path for c in out_with.changes if c.deleted]
    deleted_paths_without = [c.path for c in out_without.changes if c.deleted]
    assert "a.md" in deleted_paths_with
    assert "a.md" not in deleted_paths_without
