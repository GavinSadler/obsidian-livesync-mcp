"""Note CRUD tools exposed via MCP."""

from __future__ import annotations

from typing import Any

from ..livesync.notes import NoteRepository


class NoteTools:
    """Adapters from MCP tool calls to NoteRepository methods."""

    def __init__(self, repo: NoteRepository) -> None:
        self.repo = repo

    async def list_notes(
        self,
        path_prefix: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def read_note(self, path: str) -> dict[str, Any]:
        raise NotImplementedError

    async def create_note(self, path: str, content: str) -> dict[str, Any]:
        raise NotImplementedError

    async def update_note(self, path: str, content: str) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_note(self, path: str) -> dict[str, Any]:
        raise NotImplementedError

    async def move_note(self, old_path: str, new_path: str) -> dict[str, Any]:
        """Rename / move a note. Atomic from the LLM's perspective."""
        raise NotImplementedError
