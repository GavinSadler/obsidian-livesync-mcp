"""Note CRUD tools exposed via MCP.

Each method takes plain Python arguments and returns a Pydantic model so
the FastMCP SDK can derive a JSON Schema the LLM sees.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..errors import (
    LiveSyncMCPError,
    NoteNotFoundError,
)
from ..livesync.notes import NoteRepository
from .models import (
    AppendNoteOutput,
    BatchReadOutput,
    DeleteNoteOutput,
    ListNotesOutput,
    NoteListItem,
    NoteModel,
    NoteReadError,
)


def _ms_to_iso(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class NoteTools:
    """Adapters from MCP tool calls to NoteRepository methods."""

    def __init__(self, repo: NoteRepository) -> None:
        self.repo = repo

    async def list_notes(
        self,
        folder: str | None = None,
        limit: int = 1000,
    ) -> ListNotesOutput:
        # Normalize trailing slash so 'projects' and 'projects/' both work.
        if folder and not folder.endswith("/"):
            folder = folder + "/"

        # Fetch unbounded, then truncate, so we can report `total` honestly.
        all_summaries = await self.repo.list_paths(path_prefix=folder, limit=None)
        truncated = all_summaries[:limit]
        return ListNotesOutput(
            notes=[
                NoteListItem(
                    path=str(item["path"]),
                    mtime=_ms_to_iso(int(item["mtime"])),
                    size_bytes=int(item["size"]),
                )
                for item in truncated
            ],
            total=len(all_summaries),
        )

    async def read_note(self, path: str) -> NoteModel:
        note = await self.repo.read(path)
        if note is None:
            raise NoteNotFoundError(f"note does not exist: {path!r}")
        return NoteModel.from_repo_note(note)

    async def read_notes(self, paths: list[str]) -> BatchReadOutput:
        """Read multiple notes in one call.

        Each path is attempted independently; failures are collected per-path
        in the result rather than raising.
        """
        notes: dict[str, NoteModel | NoteReadError] = {}
        succeeded = 0
        failed = 0

        for path in paths:
            try:
                note = await self.repo.read(path)
                if note is None:
                    notes[path] = NoteReadError(path=path, error="not found")
                    failed += 1
                else:
                    notes[path] = NoteModel.from_repo_note(note)
                    succeeded += 1
            except LiveSyncMCPError as e:
                notes[path] = NoteReadError(path=path, error=str(e))
                failed += 1

        return BatchReadOutput(notes=notes, succeeded=succeeded, failed=failed)

    async def create_note(self, path: str, content: str) -> NoteModel:
        note = await self.repo.create(path, content)
        return NoteModel.from_repo_note(note)

    async def update_note(self, path: str, content: str) -> NoteModel:
        note = await self.repo.update(path, content)
        return NoteModel.from_repo_note(note)

    async def append_note(self, path: str, content: str, separator: str = "\n") -> AppendNoteOutput:
        """Append content to the end of an existing note.

        Safer than read-then-update for incremental additions: avoids
        accidentally clobbering content. Reads current content, appends
        the separator + new content, writes back.
        """
        existing = await self.repo.read(path)
        if existing is None:
            raise NoteNotFoundError(f"note does not exist: {path!r}")

        new_content = existing.content + separator + content
        note = await self.repo.update(path, new_content)
        appended_bytes = len((separator + content).encode("utf-8"))

        model = NoteModel.from_repo_note(note)
        return AppendNoteOutput(
            path=model.path,
            content=model.content,
            frontmatter=model.frontmatter,
            tags=model.tags,
            ctime=model.ctime,
            mtime=model.mtime,
            size_bytes=model.size_bytes,
            appended_bytes=appended_bytes,
        )

    async def delete_note(self, path: str) -> DeleteNoteOutput:
        await self.repo.delete(path)
        return DeleteNoteOutput(path=path, deleted=True)

    async def move_note(self, old_path: str, new_path: str) -> NoteModel:
        """Rename / move a note. Atomic from the LLM's perspective.

        Note: wikilinks in other notes pointing to old_path are NOT
        automatically rewritten. Use get_backlinks(old_path) first if
        you need to update them, or rely on Obsidian's "auto-update
        internal links" when a user opens the vault.
        """
        note = await self.repo.move(old_path, new_path)
        return NoteModel.from_repo_note(note)
