# obsidian-livesync-mcp

A Python MCP (Model Context Protocol) server that lets an LLM read, search,
and edit notes in an Obsidian vault stored in a CouchDB database managed by
the [Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync) plugin.

The server connects directly to CouchDB — no running Obsidian instance
required.

> **Status:** scaffolding only. See [DESIGN.md](./DESIGN.md) for the running
> requirements list and current implementation status.

## Quick start

```bash
# from this directory (mcp_server/):
uv sync                  # install dependencies into .venv
uv run pytest            # run tests
uv run ruff check .      # lint
uv run mypy src          # type-check
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
