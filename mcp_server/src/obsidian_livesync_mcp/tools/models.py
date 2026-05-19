"""Pydantic models used in MCP tool input/output schemas.

These are the schemas the MCP SDK turns into JSON Schema for the LLM, so
field descriptions are normative.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def _ms_to_iso(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class NoteModel(BaseModel):
    """A note's full content and metadata."""

    path: str = Field(description="Path of the note (echoed from input).")
    content: str = Field(
        description=(
            "Full markdown content as UTF-8 text. "
            "Frontmatter (if present) has been stripped into the `frontmatter` field. "
            "Whitespace and line endings in the body are preserved as stored."
        ),
    )
    frontmatter: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Parsed YAML frontmatter from the top of the note. "
            "None if the note has no frontmatter or if parsing failed. "
            "Commonly includes metadata like tags, keywords, created, etc."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "Tags extracted from frontmatter (tags or keywords fields). "
            "None if no tags found. Lowercase, deduplicated."
        ),
    )
    ctime: datetime = Field(description="Creation time (ISO 8601, UTC).")
    mtime: datetime = Field(description="Last modification time (ISO 8601, UTC).")
    size_bytes: int = Field(
        ge=0,
        description="Plaintext size of the note in bytes (UTF-8).",
    )

    @classmethod
    def from_repo_note(cls, note: object, include_frontmatter: bool = True) -> NoteModel:
        # Imported lazily to avoid a top-level circular import.
        from ..livesync.models import extract_frontmatter, extract_tags
        from ..livesync.notes import Note

        assert isinstance(note, Note)

        frontmatter = None
        tags = None
        content = note.content

        if include_frontmatter:
            frontmatter, content = extract_frontmatter(note.content)
            if frontmatter:
                tags = extract_tags(frontmatter)

        return cls(
            path=note.path,
            content=content,
            frontmatter=frontmatter or None,
            tags=tags or None,
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


# --- Links & Backlinks ---


class LinkSearchOutput(BaseModel):
    """Result of a forward-links or backlinks query."""

    path: str = Field(description="The note path queried (echoed from input).")
    forward_links: list[str] = Field(
        default_factory=list,
        description="Paths of notes referenced by this note (outgoing [[links]]).",
    )
    backlinks: list[str] = Field(
        default_factory=list,
        description="Paths of notes that reference this note (incoming [[links]]).",
    )


# --- Keyword Search ---


class KeywordMatch(BaseModel):
    """A single match in keyword search results."""

    path: str = Field(description="Path of the note containing the match.")
    line_number: int = Field(
        ge=1,
        description="Line number in the note where the match occurs (1-indexed).",
    )
    snippet: str = Field(
        description=(
            "The matched line or excerpt around the match. "
            "Limited to ~200 characters for readability."
        ),
    )


class KeywordSearchOutput(BaseModel):
    """Results of a keyword/regex search."""

    matches: list[KeywordMatch] = Field(description="Matches ranked by note path (lexicographic).")
    total_matches: int = Field(
        ge=0,
        description="Total number of matches found (may exceed result count if truncated).",
    )


# --- Batch Operations ---


class NoteReadError(BaseModel):
    """Error details when reading a note fails."""

    path: str = Field(description="The path that could not be read.")
    error: str = Field(description="Error message (e.g., 'not found', 'decryption failed').")


class BatchReadOutput(BaseModel):
    """Results of reading multiple notes at once."""

    notes: dict[str, NoteModel | NoteReadError] = Field(
        description=(
            "Results keyed by path. Value is either a NoteModel (success) "
            "or NoteReadError (failure)."
        ),
    )
    succeeded: int = Field(ge=0, description="Number of notes successfully read.")
    failed: int = Field(ge=0, description="Number of notes that failed to read.")


class AppendNoteOutput(NoteModel):
    """Result of appending to a note."""

    appended_bytes: int = Field(
        ge=0,
        description="Number of bytes appended (including the separator newline).",
    )


# --- Recent Changes ---


class ChangeItem(BaseModel):
    """A single change event in the vault."""

    path: str = Field(description="Path of the note affected.")
    change_type: str = Field(description='Type of change: "create", "update", or "delete".')
    mtime: datetime = Field(description="Timestamp of the change (ISO 8601, UTC).")
    deleted: bool = Field(
        description="True if the note is soft-deleted. Always False if type != delete."
    )
    seq: int = Field(
        description=(
            "CouchDB sequence number. Use for resumable polling with `since_seq` parameter."
        ),
    )
    size_bytes: int | None = Field(
        default=None,
        description="Size of the note in bytes (None if deleted).",
    )


class RecentChangesOutput(BaseModel):
    """Results of querying recent vault changes."""

    changes: list[ChangeItem] = Field(description="Changes matching the query, most recent first.")
    watermark_seq: int = Field(
        description=(
            "Highest sequence number in the results. "
            "Pass as `since_seq` on the next poll for incremental updates."
        ),
    )
    total_available: int = Field(
        ge=0,
        description=(
            "Total changes matching the filter (not including watermark changes). "
            "If total_available > len(changes), results were truncated."
        ),
    )
    truncated: bool = Field(
        description=(
            "True if more results are available beyond the limit. "
            "Use `since_seq` to fetch the next batch."
        ),
    )
