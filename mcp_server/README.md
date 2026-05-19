# obsidian-livesync-mcp

A Python MCP (Model Context Protocol) server that lets an LLM read, search,
and edit notes in an Obsidian vault stored in a CouchDB database managed by
the [Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync) plugin.

The server connects directly to CouchDB — no running Obsidian instance
required.

> **Status:** MVP. All 7 MCP tools (`list_notes`, `read_note`, `create_note`,
> `update_note`, `delete_note`, `move_note`, `semantic_search`) are wired up.
> `semantic_search` is a stub (returns empty results with honest
> `index_coverage`) until we pick a vector store / embedding backend.
> **Encryption is NOT supported in this MVP** — the vault must have
> "End-to-End Encryption" disabled in the LiveSync plugin settings. See
> [DESIGN.md](./DESIGN.md) "Known gaps & compatibility risks" for the full
> list of unresolved spec questions.

## Quick start

```bash
# from this directory (mcp_server/):
uv sync                          # install dependencies into .venv
uv run pytest                    # run tests
uv run ruff format --check .     # check formatting
uv run ruff check .              # lint
uv run mypy src tests            # type-check
uv run obsidian-livesync-mcp     # run the server (stdio transport)
```

## Configuration

The server reads from environment variables (or a `.env` file in this
directory). Minimum set:

```env
COUCHDB_URL=https://your-couchdb-host:6984
COUCHDB_DATABASE=obsidian_vault
COUCHDB_USERNAME=...
COUCHDB_PASSWORD=...

# Only if your vault uses E2EE:
LIVESYNC_PASSPHRASE=...
LIVESYNC_OBFUSCATE_PATHS=true

# Vector search:
VECTOR_ENABLED=true
VECTOR_EMBEDDING_BACKEND=openai
```

## Layout

```
mcp_server/
├── DESIGN.md                       # source of truth for requirements
├── pyproject.toml                  # uv project metadata + tool config
├── src/obsidian_livesync_mcp/
│   ├── server.py                   # MCP server entry point
│   ├── config.py                   # env-driven settings
│   ├── couchdb.py                  # async CouchDB HTTP client
│   ├── livesync/                   # LiveSync schema layer
│   │   ├── models.py               # CouchDB document types
│   │   ├── paths.py                # path ↔ doc-ID encoding
│   │   ├── chunks.py               # content splitting + hashing
│   │   ├── encryption.py           # HKDF / PBKDF2 / deflate
│   │   └── notes.py                # high-level note repository
│   ├── tools/                      # MCP tool implementations
│   │   ├── notes.py                # CRUD
│   │   └── search.py               # keyword + semantic
│   └── vector/                     # vector indexing
│       ├── store.py                # backend Protocol
│       ├── embeddings.py           # embedding backend Protocol
│       └── indexer.py              # _changes-feed watcher
└── tests/
```

## How it relates to the parent repo

The parent directory is a clone of the Obsidian LiveSync plugin, and the
TypeScript code under `../src/` is the canonical reference for how notes
are encoded on disk in CouchDB. We do not depend on or build any of that
code — we just read it to understand the wire format we need to match.
