"""Pydantic models used in MCP tool input/output schemas.

These are the schemas the MCP SDK turns into JSON Schema for the LLM, so
field descriptions are normative.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _ms_to_iso(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class NoteModel(BaseModel):
    """A note's full content and metadata."""

    path: str = Field(description="Path of the note (echoed from input).")
    content: str = Field(
        description=(
            "Full markdown content as UTF-8 text. "
            "Includes any YAML frontmatter at the top, verbatim. "
            "Whitespace and line endings preserved as stored."
        ),
    )
    ctime: datetime = Field(description="Creation time (ISO 8601, UTC).")
    mtime: datetime = Field(description="Last modification time (ISO 8601, UTC).")
    size_bytes: int = Field(
        ge=0,
        description="Plaintext size of the note in bytes (UTF-8).",
    )

    @classmethod
    def from_repo_note(cls, note: object) -> NoteModel:
        # Imported lazily to avoid a top-level circular import.
        from ..livesync.notes import Note

        assert isinstance(note, Note)
        return cls(
            path=note.path,
            content=note.content,
            ctime=_ms_to_iso(note.ctime),
            mtime=_ms_to_iso(note.mtime),
            size_bytes=note.size,
        )


class NoteListItem(BaseModel):
    """A single note's summary for listing."""

    path: str = Field(
        description=(
            "File path relative to the vault root. Examples: 'README.md', 'daily/2026-05-14.md'."
        ),
    )
    mtime: datetime = Field(description="Last modification time (ISO 8601, UTC).")
    size_bytes: int = Field(ge=0, description="Plaintext size of the note in bytes.")


class ListNotesOutput(BaseModel):
    """Notes matching the filter, ordered lexicographically by path."""

    notes: list[NoteListItem] = Field(
        description=(
            "Matching notes, sorted by path (case-sensitive lexicographic). "
            "May be truncated to `limit` items — check `total` to know."
        ),
    )
    total: int = Field(
        ge=0,
        description=(
            "Total notes matching the folder filter (independent of limit). "
            "If total > len(notes), the result was truncated."
        ),
    )


class DeleteNoteOutput(BaseModel):
    """Confirmation that a note has been soft-deleted."""

    path: str = Field(description="Path of the deleted note (echoed from input).")
    deleted: bool = Field(
        default=True,
        description="Always true on success.",
    )


# --- semantic_search ---


class SearchHit(BaseModel):
    """A single search result — a matched text chunk with context."""

    path: str = Field(description="Path of the note containing this match.")
    heading: str | None = Field(
        default=None,
        description=(
            "Nearest markdown heading above the match. "
            "None if the match appears before any heading."
        ),
    )
    snippet: str = Field(
        description="Matched text snippet (first ~200 characters of the chunk).",
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarity score [0, 1]. 1.0 is highest relevance.",
    )
    mtime: datetime = Field(
        description="Last modification time of the note (ISO 8601, UTC).",
    )
    size_bytes: int = Field(
        ge=0,
        description="Plaintext size of the note in bytes (UTF-8).",
    )


class IndexCoverage(BaseModel):
    """Coverage statistics of the semantic search index."""

    indexed: int = Field(
        ge=0,
        description=(
            "Number of notes currently indexed and searchable. "
            "May be less than total if indexing is still in progress."
        ),
    )
    total: int = Field(
        ge=0,
        description=(
            "Total notes in the vault. If indexed < total, the index "
            "is still being built and results may be incomplete."
        ),
    )


class SemanticSearchOutput(BaseModel):
    """Results of a semantic search query."""

    results: list[SearchHit] = Field(
        description=(
            "Matched chunks ranked by score (highest first). "
            "Empty if nothing matched, or if the index is not yet built — "
            "check index_coverage to tell those cases apart."
        ),
    )
    index_coverage: IndexCoverage = Field(
        description=("Index status. If indexed < total, the index is still building."),
    )
