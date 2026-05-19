"""Note CRUD tools exposed via MCP.

Each method takes plain Python arguments and returns a Pydantic model so
the FastMCP SDK can derive a JSON Schema the LLM sees.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..errors import NoteNotFoundError
from ..livesync.notes import NoteRepository
from .models import (
    DeleteNoteOutput,
    ListNotesOutput,
    NoteListItem,
    NoteModel,
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

    async def create_note(self, path: str, content: str) -> NoteModel:
        note = await self.repo.create(path, content)
        return NoteModel.from_repo_note(note)

    async def update_note(self, path: str, content: str) -> NoteModel:
        note = await self.repo.update(path, content)
        return NoteModel.from_repo_note(note)

    async def delete_note(self, path: str) -> DeleteNoteOutput:
        await self.repo.delete(path)
        return DeleteNoteOutput(path=path, deleted=True)

    async def move_note(self, old_path: str, new_path: str) -> NoteModel:
        """Rename / move a note. Atomic from the LLM's perspective."""
        note = await self.repo.move(old_path, new_path)
        return NoteModel.from_repo_note(note)
