"""Search tools exposed via MCP."""

from __future__ import annotations

from ..livesync.notes import NoteRepository
from ..vector.store import VectorStore
from .models import IndexCoverage, SemanticSearchOutput


class SearchTools:
    """MCP adapter for semantic search.

    In the MVP this is a stub: it returns an empty result set with an
    honest `index_coverage` reflecting "0 indexed of N total". The LLM
    can tell the index isn't built yet without crashing the tool call.

    Real implementation requires:
      - A vector store (sqlite-vec, Chroma, etc.) wired up via config.
      - An embedding backend (OpenAI, sentence-transformers, etc.).
      - A background _changes subscriber that indexes new/changed notes.

    Tracked as a follow-up in DESIGN.md.
    """

    def __init__(self, repo: NoteRepository, vectors: VectorStore | None) -> None:
        self.repo = repo
        self.vectors = vectors

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
    ) -> SemanticSearchOutput:
        # Without an indexer in the MVP, indexed=0 always. `total` is the
        # count of live notes in the vault.
        all_notes = await self.repo.list_paths(limit=None)
        total = len(all_notes)
        return SemanticSearchOutput(
            results=[],
            index_coverage=IndexCoverage(indexed=0, total=total),
        )
