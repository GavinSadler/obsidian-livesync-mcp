"""Bidirectional wikilink graph for backlinks and forward-link queries.

Maintains an in-memory graph of note-to-note links, updated incrementally
from the CouchDB _changes feed. Enables fast backlinks queries and forward-link
extraction without rescanning the entire vault.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class LinkRef:
    """A reference to another note via wikilink."""

    target: str  # resolved note path
    raw_text: str  # the [[link|alias]] text as written
    is_alias: bool = False  # True if link had a pipe (|)


@dataclass
class LinkGraph:
    """Bidirectional graph of wikilinks in the vault.

    - `forward_links[path]` = set of paths that this note references
    - `backlinks[path]` = set of paths that reference this note
    - `seq` = last CouchDB _changes sequence number processed
    """

    forward_links: dict[str, set[str]] = field(default_factory=dict)
    backlinks: dict[str, set[str]] = field(default_factory=dict)
    seq: int = 0

    def add_or_update(self, path: str, content: str) -> None:
        """Parse a note's content and update both forward and backlink indexes.

        Extracts all wikilinks from the content, removes the note from any
        previous backlinks (if updating), and re-adds it.
        """
        # Extract wikilinks from content
        extracted = extract_wikilinks(content)
        new_targets = {target for target, _ in extracted}

        # Remove old backlinks for this note (if it existed)
        if path in self.forward_links:
            old_targets = self.forward_links[path]
            for old_target in old_targets:
                if old_target not in new_targets:
                    self.backlinks.setdefault(old_target, set()).discard(path)

        # Update forward links
        self.forward_links[path] = new_targets

        # Add new backlinks
        for target in new_targets:
            self.backlinks.setdefault(target, set()).add(path)

    def delete(self, path: str) -> None:
        """Remove a note from the graph."""
        # Remove from all backlink sets it was in
        if path in self.forward_links:
            for target in self.forward_links[path]:
                self.backlinks.get(target, set()).discard(path)
            del self.forward_links[path]

        # Remove its backlinks entry
        self.backlinks.pop(path, None)

    def get_forward_links(self, path: str) -> set[str]:
        """Return all notes that this path references."""
        return self.forward_links.get(path, set()).copy()

    def get_backlinks(self, path: str) -> set[str]:
        """Return all notes that reference this path."""
        return self.backlinks.get(path, set()).copy()

    def rebuild_from_notes(self, notes: dict[str, str]) -> None:
        """Rebuild the entire graph from a dict of {path: content}.

        Used on startup to index all notes in the vault.
        Clears existing graph.
        """
        self.forward_links.clear()
        self.backlinks.clear()

        for path, content in notes.items():
            self.add_or_update(path, content)

    def update_from_change(self, path: str, content: str | None) -> None:
        """Apply a single CouchDB _changes event.

        If content is None, the note was deleted. Otherwise, update/add it.
        """
        if content is None:
            self.delete(path)
        else:
            self.add_or_update(path, content)


def extract_wikilinks(content: str) -> list[tuple[str, str]]:
    """Extract wikilink targets from markdown content.

    Finds all [[target]], [[target|alias]], and [[target#heading]] patterns.
    Returns list of (resolved_target, raw_text) tuples.

    Note: Does not resolve basenames to full paths — that requires vault
    knowledge (list of all note paths). The caller must resolve using
    `resolve_basename_to_path()`.
    """
    pattern = r"\[\[([^\[\]|#]+)(?:[#|]([^\[\]]*))?\]\]"
    matches = re.findall(pattern, content)

    results = []
    for target, suffix in matches:
        target = target.strip()
        if not target:
            continue

        # If there's a suffix (pipe or #), include it in raw_text
        if suffix:
            raw_text = (
                f"[[{target}|{suffix}]]"
                if "|" in f"[[{target}|{suffix}]]"
                else f"[[{target}#{suffix}]]"
            )
        else:
            raw_text = f"[[{target}]]"

        results.append((target, raw_text))

    return results


def resolve_basename_to_path(
    basename: str, all_note_paths: set[str], case_sensitive: bool = False
) -> str | None:
    """Resolve a wikilink basename to a full note path.

    Follows Obsidian's resolution: find any note whose filename (not full path)
    matches the basename, case-insensitive by default. If multiple matches,
    prefer the shortest path.

    Returns None if no match found.
    """
    basename_cmp = basename if case_sensitive else basename.lower()

    matches = []
    for path in all_note_paths:
        filename = path.split("/")[-1]
        filename_cmp = filename if case_sensitive else filename.lower()

        # Support both [[name]] and [[name.md]]
        if filename_cmp == basename_cmp or filename_cmp == f"{basename_cmp}.md":
            matches.append(path)

    if not matches:
        return None

    # Return shortest path (Obsidian's tiebreaker)
    return min(matches, key=len)
