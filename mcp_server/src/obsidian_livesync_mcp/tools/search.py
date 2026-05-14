"""Search tools (keyword + semantic) exposed via MCP."""

from __future__ import annotations

from typing import Any

from ..livesync.notes import NoteRepository
from ..vector.store import VectorStore


class SearchTools:
    def __init__(self, repo: NoteRepository, vectors: VectorStore | None) -> None:
        self.repo = repo
        self.vectors = vectors

    async def search_notes(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Keyword search by scanning note content."""
        raise NotImplementedError

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        path_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def find_related_notes(
        self,
        path: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        keyword_weight: float = 0.3,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
