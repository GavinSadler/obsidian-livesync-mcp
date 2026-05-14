"""Search tools exposed via MCP."""

from __future__ import annotations

from typing import Any

from ..livesync.notes import NoteRepository
from ..vector.store import VectorStore


class SearchTools:
    def __init__(self, repo: NoteRepository, vectors: VectorStore) -> None:
        self.repo = repo
        self.vectors = vectors

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        path_filter: str | None = None,
    ) -> dict[str, Any]:
        """Vector similarity search over indexed note chunks.

        Returns a dict with:
          - results: list of {path, heading, snippet, score}
          - index_coverage: {indexed, total} so the caller can tell
            "no matches" from "index not yet built".
        """
        raise NotImplementedError
