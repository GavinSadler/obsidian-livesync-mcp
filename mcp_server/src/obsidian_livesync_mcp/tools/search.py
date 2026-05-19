"""Search tools exposed via MCP."""

from __future__ import annotations

import re

from ..errors import LiveSyncMCPError
from ..livesync.notes import NoteRepository
from ..vector.store import VectorStore
from .models import IndexCoverage, KeywordMatch, KeywordSearchOutput, SemanticSearchOutput

MAX_SNIPPET_LENGTH = 200


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

    async def keyword_search(
        self,
        pattern: str,
        case_sensitive: bool = False,
        regex: bool = False,
        path_prefix: str | None = None,
        limit: int = 100,
    ) -> KeywordSearchOutput:
        """Search note contents for a literal string or regex pattern.

        Iterates over all live notes (filtered by `path_prefix` if given),
        decrypts as needed, and matches each note's content line-by-line.

        Args:
            pattern: Search term. If `regex=False`, treated as a literal string
                (re.escape applied). If `regex=True`, treated as a regex.
            case_sensitive: Whether matching is case-sensitive (default False).
            regex: If True, treat pattern as a regex (default False = literal).
            path_prefix: Restrict search to notes under this prefix.
            limit: Max results to return (default 100, max 1000).
        """
        if not pattern:
            return KeywordSearchOutput(matches=[], total_matches=0)

        limit = min(limit, 1000)

        if regex:
            try:
                regex_pattern = re.compile(
                    pattern, re.MULTILINE | (0 if case_sensitive else re.IGNORECASE)
                )
            except re.error as e:
                raise LiveSyncMCPError(f"invalid regex pattern: {e}") from e
        else:
            regex_pattern = re.compile(
                re.escape(pattern),
                re.MULTILINE | (0 if case_sensitive else re.IGNORECASE),
            )

        summaries = await self.repo.list_paths(path_prefix=path_prefix, limit=None)
        matches: list[KeywordMatch] = []
        total = 0

        for summary in summaries:
            path = str(summary["path"])
            try:
                note = await self.repo.read(path)
            except LiveSyncMCPError:
                continue
            if note is None:
                continue

            for line_num, line in enumerate(note.content.splitlines(), start=1):
                if regex_pattern.search(line):
                    total += 1
                    if len(matches) < limit:
                        snippet = line.strip()
                        if len(snippet) > MAX_SNIPPET_LENGTH:
                            snippet = snippet[: MAX_SNIPPET_LENGTH - 3] + "..."
                        matches.append(
                            KeywordMatch(
                                path=path,
                                line_number=line_num,
                                snippet=snippet,
                            )
                        )

        return KeywordSearchOutput(matches=matches, total_matches=total)
