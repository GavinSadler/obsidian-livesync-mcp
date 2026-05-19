"""MCP server entry point.

Wires together: configuration → CouchDB client → NoteRepository →
{NoteTools, SearchTools} → MCP server. The background indexer is not
started in the MVP (semantic_search returns honest "index not built").
"""

from __future__ import annotations

import anyio
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import Settings, load_settings
from .couchdb import CouchDBClient
from .livesync.notes import NoteRepository
from .tools.models import (
    DeleteNoteOutput,
    ListNotesOutput,
    NoteModel,
    SemanticSearchOutput,
)
from .tools.notes import NoteTools
from .tools.search import SearchTools


def build_server(settings: Settings) -> FastMCP:
    """Create a FastMCP server with all 7 tools registered."""
    mcp = FastMCP("obsidian-livesync-mcp")

    couch = CouchDBClient(
        url=settings.couchdb.url,
        database=settings.couchdb.database,
        username=settings.couchdb.username,
        password=(
            settings.couchdb.password.get_secret_value()
            if settings.couchdb.password is not None
            else None
        ),
        verify_tls=settings.couchdb.verify_tls,
    )

    repo = NoteRepository(
        couch,
        passphrase=(
            settings.livesync.passphrase.get_secret_value()
            if settings.livesync.passphrase is not None
            else None
        ),
        obfuscate_paths=settings.livesync.obfuscate_paths,
    )
    note_tools = NoteTools(repo)
    search_tools = SearchTools(repo, vectors=None)

    @mcp.tool(description="List notes in the vault, optionally scoped to a folder.")
    async def list_notes(
        folder: str | None = Field(
            default=None,
            description=(
                "Optional folder to list notes from, recursively. "
                "Examples: 'projects/', 'daily/2026/'. "
                "Null/empty means list from the vault root."
            ),
        ),
        limit: int = Field(
            default=1000,
            ge=1,
            le=5000,
            description=(
                "Maximum notes to return (default 1000, cap 5000). "
                "If total > len(notes), narrow the folder or use semantic_search."
            ),
        ),
    ) -> ListNotesOutput:
        return await note_tools.list_notes(folder=folder, limit=limit)

    @mcp.tool(description="Read a single note's full markdown content by path.")
    async def read_note(
        path: str = Field(
            description=(
                "Path to the note, relative to vault root. Forward slashes, case-sensitive."
            ),
        ),
    ) -> NoteModel:
        return await note_tools.read_note(path)

    @mcp.tool(
        description=(
            "Create a new note. Fails if a non-deleted note already exists at the path. "
            "A soft-deleted note is silently overwritten (resurrected)."
        ),
    )
    async def create_note(
        path: str = Field(
            description=(
                "New note path, relative to vault root. Must end in '.md'. "
                "Parent folders are implicit."
            ),
        ),
        content: str = Field(
            description=(
                "Initial markdown content. May be empty. May include YAML frontmatter at the top."
            ),
        ),
    ) -> NoteModel:
        return await note_tools.create_note(path, content)

    @mcp.tool(
        description=(
            "Overwrite an existing note's content. Strict — fails if the note "
            "doesn't exist (use create_note for that)."
        ),
    )
    async def update_note(
        path: str = Field(description="Path of the note to update."),
        content: str = Field(description="New full markdown content."),
    ) -> NoteModel:
        return await note_tools.update_note(path, content)

    @mcp.tool(
        description=(
            "Soft-delete a note (sets deleted: true). The doc remains in "
            "CouchDB as a tombstone and can be revived by create_note."
        ),
    )
    async def delete_note(
        path: str = Field(description="Path of the note to soft-delete."),
    ) -> DeleteNoteOutput:
        return await note_tools.delete_note(path)

    @mcp.tool(description="Rename or move a note. Bumps mtime; ctime is fresh.")
    async def move_note(
        old_path: str = Field(description="Current path of the note."),
        new_path: str = Field(
            description="Destination path. Must end in '.md', must not already exist.",
        ),
    ) -> NoteModel:
        return await note_tools.move_note(old_path, new_path)

    @mcp.tool(
        description=(
            "Semantic (vector) similarity search over indexed note chunks. "
            "Returns matched chunks with snippets and similarity scores. "
            "Check index_coverage to tell 'no matches' from 'index not yet built'."
        ),
    )
    async def semantic_search(
        query: str = Field(
            min_length=1,
            max_length=2000,
            description="Search query: phrase, question, or keywords.",
        ),
        top_k: int = Field(
            default=5,
            ge=1,
            le=50,
            description="Maximum results to return (default 5, cap 50).",
        ),
    ) -> SemanticSearchOutput:
        return await search_tools.semantic_search(query=query, top_k=top_k)

    # Stash the CouchDB client on the server so we can close it on shutdown.
    mcp._couch_client_for_shutdown = couch  # type: ignore[attr-defined]
    return mcp


async def _serve(mcp: FastMCP) -> None:
    try:
        await mcp.run_stdio_async()
    finally:
        couch = getattr(mcp, "_couch_client_for_shutdown", None)
        if couch is not None:
            await couch.close()


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    settings = load_settings()
    mcp = build_server(settings)
    anyio.run(_serve, mcp)


if __name__ == "__main__":
    main()
