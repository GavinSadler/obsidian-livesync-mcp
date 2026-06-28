"""Wikilink tools: backlinks and forward links exposed via MCP."""

from __future__ import annotations

from ..livesync.links import LinkGraph
from ..livesync.notes import NoteRepository
from .models import LinkSearchOutput


class LinkTools:
    """Adapters for LinkGraph queries.

    The graph is kept up-to-date by a background _changes subscriber
    (see server.py); these tools just query the current state.
    """

    def __init__(self, graph: LinkGraph, repo: NoteRepository) -> None:
        self.graph = graph
        self.repo = repo

    async def get_forward_links(self, path: str) -> LinkSearchOutput:
        """Return notes that this note links to (outgoing [[...]] references)."""
        targets = self.graph.get_forward_links(path)
        return LinkSearchOutput(
            path=path,
            forward_links=sorted(targets),
            backlinks=[],
        )

    async def get_backlinks(self, path: str) -> LinkSearchOutput:
        """Return notes that reference this note (incoming [[...]] references)."""
        sources = self.graph.get_backlinks(path)
        return LinkSearchOutput(
            path=path,
            forward_links=[],
            backlinks=sorted(sources),
        )

    async def get_links(self, path: str) -> LinkSearchOutput:
        """Return both forward and backlinks in a single call."""
        return LinkSearchOutput(
            path=path,
            forward_links=sorted(self.graph.get_forward_links(path)),
            backlinks=sorted(self.graph.get_backlinks(path)),
        )
