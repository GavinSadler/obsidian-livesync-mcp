"""MCP server entry point.

Wires together: configuration → CouchDB client → NoteRepository →
{NoteTools, SearchTools, LinkTools, HistoryTools} → MCP server.

On startup, the link graph is backfilled from the vault so backlinks
and forward-link queries return correct results from the first call.
A background _changes subscriber keeps the graph fresh while the
server runs.

The semantic_search vector indexer is not started in the MVP
(semantic_search returns honest "index not built").
"""

from __future__ import annotations

import logging
from typing import Annotated

import anyio
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import Settings, load_settings
from .couchdb import CouchDBClient
from .errors import LiveSyncMCPError
from .livesync.links import LinkGraph
from .livesync.notes import NoteRepository, fetch_pbkdf2_salt
from .tools.history import HistoryTools
from .tools.links import LinkTools
from .tools.models import (
    AppendNoteOutput,
    BatchReadOutput,
    DeleteNoteOutput,
    KeywordSearchOutput,
    LinkSearchOutput,
    ListNotesOutput,
    NoteModel,
    RecentChangesOutput,
    SemanticSearchOutput,
)
from .tools.notes import NoteTools
from .tools.search import SearchTools

logger = logging.getLogger(__name__)


def build_server(settings: Settings) -> FastMCP:
    """Create a FastMCP server with all 14 tools registered."""
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
    graph = LinkGraph()
    note_tools = NoteTools(repo)
    search_tools = SearchTools(repo, vectors=None)
    link_tools = LinkTools(graph, repo)
    history_tools = HistoryTools(couch)

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
            "Read multiple notes in one call. Per-path errors are reported in the result "
            "(not raised), so a missing path won't fail the whole batch."
        ),
    )
    async def read_notes(
        paths: Annotated[
            list[str],
            Field(description="Paths of notes to read, relative to vault root."),
        ],
    ) -> BatchReadOutput:
        return await note_tools.read_notes(paths)

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
            "Append content to the end of an existing note. Safer than "
            "read-then-update for incremental additions — avoids accidentally "
            "clobbering content. Fails if the note doesn't exist."
        ),
    )
    async def append_note(
        path: str = Field(description="Path of the note to append to."),
        content: str = Field(description="Content to append."),
        separator: str = Field(
            default="\n",
            description="Separator inserted between existing content and new content.",
        ),
    ) -> AppendNoteOutput:
        return await note_tools.append_note(path, content, separator=separator)

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

    @mcp.tool(
        description=(
            "Rename or move a note. Bumps mtime; ctime is fresh. "
            "Wikilinks in other notes pointing to old_path are NOT auto-rewritten."
        ),
    )
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

    @mcp.tool(
        description=(
            "Keyword or regex search over note content. Returns matched lines "
            "with line numbers and snippets. Use regex=True for pattern matching."
        ),
    )
    async def keyword_search(
        pattern: str = Field(
            min_length=1,
            description="Search term. Literal substring by default; regex if regex=True.",
        ),
        case_sensitive: bool = Field(
            default=False,
            description="If True, match exact case.",
        ),
        regex: bool = Field(
            default=False,
            description="If True, interpret pattern as a regex. Invalid regex raises an error.",
        ),
        path_prefix: str | None = Field(
            default=None,
            description="Restrict search to notes under this prefix (e.g. 'projects/').",
        ),
        limit: int = Field(
            default=100,
            ge=1,
            le=1000,
            description="Maximum matches to return (default 100, cap 1000).",
        ),
    ) -> KeywordSearchOutput:
        return await search_tools.keyword_search(
            pattern=pattern,
            case_sensitive=case_sensitive,
            regex=regex,
            path_prefix=path_prefix,
            limit=limit,
        )

    @mcp.tool(
        description=(
            "Notes that this note links to (outgoing [[wikilinks]]). "
            "Backed by an in-memory graph kept fresh by the _changes feed."
        ),
    )
    async def get_forward_links(
        path: str = Field(description="Path of the note to query."),
    ) -> LinkSearchOutput:
        return await link_tools.get_forward_links(path)

    @mcp.tool(
        description=(
            "Notes that reference this note (incoming [[wikilinks]]). "
            "Backed by an in-memory graph kept fresh by the _changes feed."
        ),
    )
    async def get_backlinks(
        path: str = Field(description="Path or basename of the note to query."),
    ) -> LinkSearchOutput:
        return await link_tools.get_backlinks(path)

    @mcp.tool(description="Both forward and backlinks for a note in one call.")
    async def get_links(
        path: str = Field(description="Path of the note to query."),
    ) -> LinkSearchOutput:
        return await link_tools.get_links(path)

    @mcp.tool(
        description=(
            "Recent vault changes (create / update / delete). "
            "For resumable polling, save the returned watermark_seq and pass it as "
            "since_seq on the next call."
        ),
    )
    async def recent_changes(
        since_seq: int | None = Field(
            default=None,
            description=(
                "CouchDB sequence number to start from. Preferred for resumable polling. "
                "Wins over `since` if both given."
            ),
        ),
        since: str | None = Field(
            default=None,
            description=(
                "Timestamp filter: '1h', '30m', '7d' (relative), or ISO 8601. "
                "Uses mtime; clock-skew sensitive — prefer since_seq when possible."
            ),
        ),
        path_prefix: str | None = Field(
            default=None,
            description="Restrict to notes under this prefix.",
        ),
        include_deleted: bool = Field(
            default=True,
            description="If False, omit soft-deleted notes from results.",
        ),
        change_types: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="Filter by type: ['create', 'update', 'delete']. None = all types.",
            ),
        ] = None,
        limit: int = Field(
            default=100,
            ge=1,
            le=1000,
            description="Max events to return (default 100, cap 1000).",
        ),
    ) -> RecentChangesOutput:
        return await history_tools.recent_changes(
            since_seq=since_seq,
            since=since,
            path_prefix=path_prefix,
            include_deleted=include_deleted,
            change_types=change_types,
            limit=limit,
        )

    # Stash references so _serve can finish async setup (salt fetch +
    # link-graph backfill), run the background _changes subscriber, and
    # close the client on shutdown.
    mcp._couch_client_for_shutdown = couch  # type: ignore[attr-defined]
    mcp._repo_for_async_setup = repo  # type: ignore[attr-defined]
    mcp._link_graph_for_async_setup = graph  # type: ignore[attr-defined]
    return mcp


async def _backfill_link_graph(graph: LinkGraph, repo: NoteRepository) -> None:
    """Read every note in the vault and populate the LinkGraph.

    Errors reading individual notes are logged and skipped — better
    to have a partial graph than to fail startup.
    """
    summaries = await repo.list_paths(limit=None)
    notes: dict[str, str] = {}
    for summary in summaries:
        path = str(summary["path"])
        try:
            note = await repo.read(path)
        except LiveSyncMCPError as e:
            logger.warning("backfill: failed to read %r: %s", path, e)
            continue
        if note is not None:
            notes[path] = note.content
    graph.rebuild_from_notes(notes)
    logger.info("link graph backfill: indexed %d notes", len(notes))


async def _changes_subscriber(
    graph: LinkGraph,
    repo: NoteRepository,
    couch: CouchDBClient,
) -> None:
    """Background task: keep LinkGraph in sync with the _changes feed.

    Streams the feed indefinitely. On network/decode errors, waits
    briefly and reconnects from the last processed sequence.
    """
    while True:
        try:
            async for row in couch.changes(since=str(graph.seq), feed="continuous"):
                seq = row.get("seq")
                if not isinstance(seq, int):
                    continue
                doc = row.get("doc") or {}
                doc_id = str(doc.get("_id", ""))
                if doc_id.startswith(("_", "h:")):
                    graph.seq = seq
                    continue
                if doc.get("type") not in ("plain", "newnote"):
                    graph.seq = seq
                    continue
                path = doc.get("path")
                if not isinstance(path, str) or not path:
                    graph.seq = seq
                    continue
                if doc.get("deleted") is True or doc.get("_deleted") is True:
                    graph.delete(path)
                else:
                    try:
                        note = await repo.read(path)
                    except LiveSyncMCPError as e:
                        logger.warning("subscriber: failed to read %r: %s", path, e)
                        graph.seq = seq
                        continue
                    if note is not None:
                        graph.add_or_update(path, note.content)
                graph.seq = seq
        except Exception as e:
            logger.warning("changes subscriber error: %s; retrying in 5s", e)
            await anyio.sleep(5)


async def _serve(mcp: FastMCP) -> None:
    couch: CouchDBClient | None = getattr(mcp, "_couch_client_for_shutdown", None)
    repo: NoteRepository | None = getattr(mcp, "_repo_for_async_setup", None)
    graph: LinkGraph | None = getattr(mcp, "_link_graph_for_async_setup", None)
    try:
        # When a passphrase is configured, fetch the vault's PBKDF2 salt
        # from CouchDB before serving any requests. If the salt isn't
        # available yet, encrypted reads/writes will fail loudly later;
        # we don't block startup since the user may want to inspect a
        # vault that hasn't yet been initialised on the plugin side.
        if repo is not None and couch is not None and repo.needs_encryption:
            salt = await fetch_pbkdf2_salt(couch)
            if salt is not None:
                repo.set_pbkdf2_salt(salt)
        # Backfill the link graph synchronously so backlink and
        # forward-link queries are accurate from the first request.
        if repo is not None and graph is not None:
            try:
                await _backfill_link_graph(graph, repo)
            except Exception as e:
                logger.warning("link graph backfill failed: %s", e)

        # Run the MCP server and the _changes subscriber concurrently.
        # When the stdio loop exits, cancel the subscriber.
        if couch is not None and repo is not None and graph is not None:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_changes_subscriber, graph, repo, couch)
                await mcp.run_stdio_async()
                tg.cancel_scope.cancel()
        else:
            await mcp.run_stdio_async()
    finally:
        if couch is not None:
            await couch.close()


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    settings = load_settings()
    mcp = build_server(settings)
    anyio.run(_serve, mcp)


if __name__ == "__main__":
    main()
