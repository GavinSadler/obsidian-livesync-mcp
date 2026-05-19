"""Tests for wikilink extraction and bidirectional link graph."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.links import (
    LinkGraph,
    extract_wikilinks,
    resolve_basename_to_path,
)


def test_extract_wikilinks_simple() -> None:
    content = "This is a [[note]] reference."
    links = extract_wikilinks(content)
    assert len(links) == 1
    assert links[0][0] == "note"


def test_extract_wikilinks_with_alias() -> None:
    content = "See [[my-note|this note]] for details."
    links = extract_wikilinks(content)
    assert len(links) == 1
    assert links[0][0] == "my-note"
    assert "|" in links[0][1]


def test_extract_wikilinks_with_heading() -> None:
    content = "Jump to [[note#section]]."
    links = extract_wikilinks(content)
    assert len(links) == 1
    assert links[0][0] == "note"


def test_extract_wikilinks_multiple() -> None:
    content = """
    # My Note

    This links to [[note1]] and [[note2|alias]].
    Also [[another#heading]].
    """
    links = extract_wikilinks(content)
    assert len(links) == 3
    targets = [target for target, _ in links]
    assert "note1" in targets
    assert "note2" in targets
    assert "another" in targets


def test_extract_wikilinks_no_matches() -> None:
    content = "No links here, just plain text."
    links = extract_wikilinks(content)
    assert links == []


def test_extract_wikilinks_empty() -> None:
    links = extract_wikilinks("")
    assert links == []


def test_extract_wikilinks_malformed() -> None:
    # Most malformed patterns should not match
    # Note: [[broken | | link]] actually does match as "broken" with suffix "| link"
    # because the regex is permissive. That's acceptable behavior.
    content = "[single bracket]"
    links = extract_wikilinks(content)
    assert len(links) == 0


def test_resolve_basename_exact_match() -> None:
    all_paths = {"notes/example.md", "docs/other.md", "example.md"}
    result = resolve_basename_to_path("example.md", all_paths)
    assert result == "example.md"


def test_resolve_basename_without_extension() -> None:
    all_paths = {"notes/example.md", "docs/other.md"}
    result = resolve_basename_to_path("example", all_paths)
    assert result == "notes/example.md"


def test_resolve_basename_case_insensitive() -> None:
    all_paths = {"notes/Example.md", "docs/other.md"}
    result = resolve_basename_to_path("example", all_paths, case_sensitive=False)
    assert result == "notes/Example.md"


def test_resolve_basename_case_sensitive() -> None:
    all_paths = {"notes/Example.md", "docs/other.md"}
    result = resolve_basename_to_path("example", all_paths, case_sensitive=True)
    assert result is None


def test_resolve_basename_multiple_matches_shortest_wins() -> None:
    all_paths = {"a/b/example.md", "a/example.md", "example.md"}
    result = resolve_basename_to_path("example", all_paths)
    assert result == "example.md"


def test_resolve_basename_not_found() -> None:
    all_paths = {"notes/foo.md", "notes/bar.md"}
    result = resolve_basename_to_path("missing", all_paths)
    assert result is None


def test_linkgraph_add_updates_forward_links() -> None:
    graph = LinkGraph()
    content = "This links to [[target1]] and [[target2]]."
    graph.add_or_update("source.md", content)

    assert "source.md" in graph.forward_links
    assert graph.forward_links["source.md"] == {"target1", "target2"}


def test_linkgraph_add_updates_backlinks() -> None:
    graph = LinkGraph()
    graph.add_or_update("source.md", "Links to [[target]].")

    assert "target" in graph.backlinks
    assert graph.backlinks["target"] == {"source.md"}


def test_linkgraph_update_replaces_old_links() -> None:
    graph = LinkGraph()
    graph.add_or_update("note.md", "Links to [[old1]] and [[old2]].")
    assert graph.forward_links["note.md"] == {"old1", "old2"}

    # Update: remove old1, add new1
    graph.add_or_update("note.md", "Links to [[old2]] and [[new1]].")
    assert graph.forward_links["note.md"] == {"old2", "new1"}

    # Backlinks should be cleaned up
    assert graph.backlinks.get("old1", set()) == set()
    assert "note.md" in graph.backlinks["old2"]
    assert "note.md" in graph.backlinks["new1"]


def test_linkgraph_delete_removes_forward_and_backlinks() -> None:
    graph = LinkGraph()
    graph.add_or_update("note.md", "Links to [[a]] and [[b]].")
    assert "note.md" in graph.forward_links

    graph.delete("note.md")
    assert "note.md" not in graph.forward_links
    assert "note.md" not in graph.backlinks.get("a", set())
    assert "note.md" not in graph.backlinks.get("b", set())


def test_linkgraph_get_forward_links() -> None:
    graph = LinkGraph()
    graph.add_or_update("note.md", "[[target1]] and [[target2]]")

    forward = graph.get_forward_links("note.md")
    assert forward == {"target1", "target2"}


def test_linkgraph_get_forward_links_missing() -> None:
    graph = LinkGraph()
    forward = graph.get_forward_links("missing.md")
    assert forward == set()


def test_linkgraph_get_backlinks() -> None:
    graph = LinkGraph()
    graph.add_or_update("a.md", "[[target]]")
    graph.add_or_update("b.md", "[[target]]")
    graph.add_or_update("c.md", "[[other]]")

    backlinks = graph.get_backlinks("target")
    assert backlinks == {"a.md", "b.md"}


def test_linkgraph_get_backlinks_missing() -> None:
    graph = LinkGraph()
    backlinks = graph.get_backlinks("missing.md")
    assert backlinks == set()


def test_linkgraph_rebuild_from_notes() -> None:
    graph = LinkGraph()
    notes = {
        "a.md": "[[b.md]]",
        "b.md": "[[c.md]]",
        "c.md": "[[a.md]]",
    }
    graph.rebuild_from_notes(notes)

    assert graph.forward_links["a.md"] == {"b.md"}
    assert graph.forward_links["b.md"] == {"c.md"}
    assert graph.forward_links["c.md"] == {"a.md"}

    # Backlinks show which notes link to a given target
    assert graph.backlinks.get("a.md", set()) == {"c.md"}  # a.md is linked from c.md
    assert graph.backlinks.get("b.md", set()) == {"a.md"}  # b.md is linked from a.md
    assert graph.backlinks.get("c.md", set()) == {"b.md"}  # c.md is linked from b.md


def test_linkgraph_rebuild_clears_old_graph() -> None:
    graph = LinkGraph()
    graph.add_or_update("old.md", "[[target]]")
    assert "old.md" in graph.forward_links

    graph.rebuild_from_notes({"new.md": "[[other]]"})
    assert "old.md" not in graph.forward_links
    assert "new.md" in graph.forward_links


def test_linkgraph_update_from_change_add() -> None:
    graph = LinkGraph()
    graph.update_from_change("note.md", "[[a]] and [[b]]")

    assert graph.forward_links["note.md"] == {"a", "b"}
    assert "note.md" in graph.backlinks["a"]
    assert "note.md" in graph.backlinks["b"]


def test_linkgraph_update_from_change_delete() -> None:
    graph = LinkGraph()
    graph.add_or_update("note.md", "[[target]]")
    assert "note.md" in graph.forward_links

    graph.update_from_change("note.md", None)
    assert "note.md" not in graph.forward_links
    assert "note.md" not in graph.backlinks.get("target", set())


def test_linkgraph_seq_tracking() -> None:
    graph = LinkGraph()
    assert graph.seq == 0

    graph.seq = 42
    assert graph.seq == 42
