# Obsidian LiveSync MCP Server — Design

A Python-based MCP (Model Context Protocol) server that lets an LLM interact
with an Obsidian vault stored in a CouchDB database managed by the
[Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync) plugin.

The server acts as a standalone client to the same CouchDB the plugin syncs
against. From the database's perspective, the MCP server is just another
LiveSync peer — but instead of a user editing notes in Obsidian, an LLM is
reading and modifying them through MCP tools.

## Goals

- Let an LLM browse, read, search, create, update, and delete notes in an
  Obsidian vault without going through Obsidian itself.
- Provide semantic (vector) search over the vault so the LLM can find
  relevant content quickly, with the index maintained server-side via the
  CouchDB `_changes` feed.
- Stay protocol-clean: no Obsidian dependency, no plugin code reused — only
  the LiveSync data schema is mirrored.

## Non-goals

- Real-time multi-peer conflict resolution. Conflicts will be detected and
  surfaced, but the server is not a full LiveSync replication node.
- Two-way Obsidian plugin integration. The server reads/writes the database
  directly.
- Hosting/managing the CouchDB instance. The user supplies connection info.

---

## Conflict semantics

CouchDB uses optimistic concurrency: every write must include the current
`_rev`, and stale writes are rejected with HTTP 409. Our server treats the
database as a normal MVCC store and **does not implement any merge logic**.
The existing LiveSync plugin on the user's devices already handles
resolution.

### What the server does

- **Read-modify-write with retry-on-409.** For every update we GET the
  doc, mutate it, PUT with the current `_rev`. If we get 409, re-fetch
  and retry (cap at a small number of retries, e.g. 3).
- **Set `mtime` correctly.** The auto-merger uses `mtime` to order
  concurrent inserts and to break ties in JSON merges, so every write
  stamps `mtime` with the current millisecond timestamp. Preserve
  `ctime` from the existing doc on updates.
- **Treat soft-deletes as terminal.** A read of a doc with
  `deleted: true` returns `None` (note not found). Writes recreate it.

### What the server does *not* do

- **No three-way merging.** Even though we know how LiveSync does it (see
  reference below), we don't reimplement it. Our writes are atomic from
  CouchDB's perspective.
- **No conflict creation by us alone.** A single client doing
  read-modify-write cannot create a `_conflicts` entry — conflicts only
  arise from replication. If the LiveSync plugin on a device later
  syncs a divergent edit of a doc we wrote, CouchDB populates
  `_conflicts`, and the *plugin's* `ConflictManager.tryAutoMerge` runs
  on the next replication pass on the user's device.
- **No interactive resolution.** If auto-merge fails on the device, the
  user is prompted in Obsidian, exactly as they would be today.

### Optional future feature

- [ ] `list_conflicts()` — surface notes with non-empty `_conflicts` so an
      LLM can summarize or help the user reason about them. Strictly
      value-added; not required for the core read/write tools.

### Reference

- `src/lib/src/managers/ConflictManager.ts` — three-way merge and
  per-line/per-key collision detection.
- `src/modules/core/ReplicateResultProcessor.ts` — where conflicts are
  picked up off the replication stream.

---

## Requirements

This is the running source of truth for what the server should do. Items
are tagged with status: `[ ]` planned, `[~]` in progress, `[x]` complete.

### MCP tools — notes (CRUD)

- [ ] `list_notes(path_prefix?, limit?, cursor?)` — list note paths in the
      vault. Supports prefix filter for folder-style browsing.
- [ ] `read_note(path)` — read full markdown content of a note. Handles
      chunk reassembly, decompression, and decryption transparently.
- [ ] `read_note_section(path, heading)` — return the content under a
      specific markdown heading (parsed client-side from the full note).
- [ ] `create_note(path, content)` — create a new note. Fails if the path
      already exists.
- [ ] `update_note(path, content)` — overwrite the content of an existing
      note. Handles chunking + metadata updates.
- [ ] `delete_note(path)` — soft-delete a note (sets `deleted: true`).

### MCP tools — search

- [ ] `search_notes(query, limit?)` — keyword search across note contents.
      Client-side scan in the absence of native FTS.
- [ ] `semantic_search(query, top_k?, path_filter?)` — vector similarity
      search over indexed note chunks.
- [ ] `find_related_notes(path, top_k?)` — find notes semantically similar
      to a given note.
- [ ] `hybrid_search(query, top_k?, keyword_weight?)` — combined keyword
      and vector ranking. *(optional / stretch)*

### MCP tools — vault & index

- [ ] `get_vault_info()` — vault metadata: CouchDB endpoint, note count,
      encryption mode, index status.
- [ ] `index_status()` — number of notes indexed, embedding model, last
      sync time, dimension.
- [ ] `reindex_vault(force?)` — rebuild the vector index from scratch.

### LiveSync schema support

- [ ] CouchDB connection (basic auth + TLS).
- [ ] Path → document ID encoding (plain mode).
- [ ] Path → document ID encoding (obfuscated `f:` mode, SHA-256 stretched).
- [ ] Chunk reassembly from `children[]` references.
- [ ] Note write path: content splitting, chunk hashing (`h:` IDs), parent
      doc with `children[]` and `eden` field.
- [ ] Soft-delete via `deleted: true`.
- [ ] Compression / decompression (fflate / deflate, `~` marker).
- [ ] Encryption V2 (HKDF, `%=` marker) — read.
- [ ] Encryption V2 (HKDF) — write.
- [ ] Encryption V1 (PBKDF2, `%` marker) — read. *(legacy, may skip)*

### Vector index

- [ ] Background subscriber to CouchDB `_changes` feed (`since=now`,
      `feed=continuous`).
- [ ] On change: fetch updated note, chunk for embedding, embed, upsert
      into vector store.
- [ ] On delete: remove vectors for that note from the store.
- [ ] Pluggable embedding backend (OpenAI, local sentence-transformers,
      Ollama, etc.) selected via config.
- [ ] Pluggable vector store (initial choice TBD — Chroma, Qdrant, sqlite-vec,
      LanceDB are candidates).
- [ ] Persistent watermark of last-seen `_changes` sequence so the indexer
      can resume cleanly across restarts.
- [ ] Initial backfill on first start (or when index is empty).

### Tooling & dev experience

- [x] uv-managed Python project.
- [x] pytest + pytest-asyncio test scaffolding.
- [x] ruff for lint + format.
- [ ] mypy or pyright type-checking in CI.
- [ ] GitHub Actions: lint, type-check, test on push/PR.
- [ ] Docker image for running the server.

---

## Architecture sketch

```
                                          ┌─────────────────────────┐
                                          │       MCP client        │
                                          │  (Claude Desktop, etc.) │
                                          └────────────┬────────────┘
                                                       │  MCP (stdio/http)
                                          ┌────────────▼────────────┐
                                          │   obsidian_livesync_mcp │
                                          │  ┌───────────────────┐  │
                                          │  │   server.py       │  │  ← MCP entry
                                          │  └─────────┬─────────┘  │
                                          │  ┌─────────▼─────────┐  │
                                          │  │   tools/          │  │  ← MCP tool handlers
                                          │  └─────────┬─────────┘  │
                                          │  ┌─────────▼─────────┐  │
                                          │  │  livesync/        │  │  ← schema, chunks,
                                          │  │   paths, chunks,  │  │     encryption, paths
                                          │  │   notes, crypto   │  │
                                          │  └─────────┬─────────┘  │
                                          │  ┌─────────▼─────────┐  │
                                          │  │   couchdb.py      │  │  ← HTTP client
                                          │  └─────────┬─────────┘  │
                                          └────────────┼────────────┘
                                                       │  HTTP
                                          ┌────────────▼────────────┐
                                          │        CouchDB          │
                                          │  (the LiveSync database)│
                                          └─────────────────────────┘

                                  ┌──────────────────┐
                                  │   vector/        │
                                  │   indexer.py     │  subscribes to _changes,
                                  │   store.py       │  writes to vector DB
                                  └──────────────────┘
```

## Open questions

- **Vector store choice.** Embedded (sqlite-vec, Chroma persistent, LanceDB)
  is simpler for self-hosting; server (Qdrant, Weaviate) scales better.
  Default to embedded; make it pluggable.
- **Embedding backend default.** OpenAI's `text-embedding-3-small` is cheap
  and good, but requires an API key. A local default (sentence-transformers
  or Ollama) avoids that but adds heavy dependencies. Probably ship without
  a default and require explicit config.
- **Chunking granularity for embeddings.** Per-note? Per-heading section?
  Per-paragraph with overlap? Probably per-heading with a fallback to
  fixed-size windows for long sections.
- **Write-side LiveSync compatibility.** Our writes need to be readable by
  the official LiveSync plugin without trouble. This means matching their
  `eden` field semantics and chunk hashing exactly. To be verified with
  round-trip tests against a real vault.
- **Search-while-indexing.** What does `semantic_search` return if the
  index is incomplete? Probably: include a `coverage` field in the
  response so the LLM knows.

## Reference

- The TypeScript source under `src/lib/` (the `livesync-commonlib`
  submodule) is the canonical reference for the on-disk schema.
- Key files when in doubt:
  - `src/lib/src/common/models/db.type.ts` — document type definitions.
  - `src/lib/src/string_and_binary/path.ts` — path ↔ ID encoding.
  - `src/lib/src/pouchdb/encryption.ts` — encryption.
  - `src/lib/src/ContentSplitter/` — chunking.
  - `src/lib/src/managers/EntryManager/EntryManagerImpls.ts` — read/write
    note assembly logic.
