# Where I left off

This is a snapshot of progress on the MVP MCP server, written so you
(or a fresh Claude session) can pick up cleanly.

## What's done

### Infrastructure
- **CI**: `.github/workflows/mcp-ci.yml` runs on PRs and pushes to main
  / claude/* branches, path-filtered to `mcp_server/**`. Three jobs:
  - `format`: `ruff format --check .` + `ruff check .`
  - `typecheck`: `mypy src tests` on Python 3.11 and 3.13
  - `test`: `pytest --cov` on Python 3.11 and 3.13
- **DESIGN.md**: complete API spec for all 7 tools with Pydantic models
  + Mermaid diagrams; updated to mark implemented items `[x]`. Includes
  a "Known gaps & compatibility risks" section tracking things to
  resolve before claiming plugin-compatibility.
- **README.md**: quick-start updated; HKDF encryption (`%=`) supported
  when `LIVESYNC_PASSPHRASE` is set.

### MVP implementation (in `mcp_server/src/obsidian_livesync_mcp/`)
- `livesync/paths.py` — plain mode + obfuscated (`f:`) mode with
  SHA-256 stretching. *Obfuscated mode needs real-vault verification.*
- `livesync/chunks.py` — line-aware splitter (max 1000 chars/chunk),
  XXHash64 chunk IDs in base36 (matches plugin default config).
- `livesync/encryption.py` — deflate compression (`~` marker) and HKDF
  V2 encryption (`%=` marker) implemented via the `cryptography`
  library. Read support for `%$` ephemeral-salt HKDF too. Legacy `%`
  (PBKDF2) and `%~` (V3) still raise `EncryptionNotSupportedError`.
- `livesync/notes.py` — `NoteRepository` with full CRUD: read-modify-write
  with 409 retry (limit 3), soft-delete via `deleted: true`, `move` as
  create-then-delete with `MovePartialFailureError` if delete fails.
  Encrypts/decrypts chunks transparently when `passphrase` +
  `pbkdf2_salt` are set. `fetch_pbkdf2_salt(couch)` helper reads the
  vault salt from `_local/obsidian_livesync_sync_parameters`.
- `couchdb.py` — async httpx-based client (`get`, `put`, `bulk_get`,
  `bulk_docs`, `all_docs`, `changes` stream).
- `errors.py` — typed exception hierarchy.
- `tools/models.py` — shared Pydantic models (`NoteModel`,
  `ListNotesOutput`, `SearchHit`, `IndexCoverage`, etc.).
- `tools/notes.py` — 6 CRUD MCP adapters.
- `tools/search.py` — `semantic_search` stub returning empty results
  with honest `index_coverage` (indexed=0, total=N).
- `server.py` — FastMCP-based entry point registering all 7 tools.
  `obsidian-livesync-mcp` console script is wired up.
- `config.py` — `load_settings()` reads from env / `.env`.

### Tests (71 total, all passing)
- `tests/fake_couch.py` — in-memory `CouchDBClient` stand-in with
  realistic MVCC semantics (rev tracking, 409 on stale writes).
- `test_paths.py` (11) — plain & obfuscated round-trips, leading-`_`
  guard, fallback-path requirement, determinism, passphrase variance.
- `test_chunks.py` (9) — split/assemble round-trip, max-size enforcement,
  hard-split for long lines, hash stability + prefix.
- `test_encryption.py` (24) — wire-format constants pinned; marker
  detection; PBKDF2 / HKDF derivation correctness + determinism; HKDF
  encrypt/decrypt round-trips (ASCII, unicode, empty); layout sanity;
  wrong-passphrase / wrong-salt rejection; dispatcher routing; legacy
  formats raise `EncryptionNotSupportedError`; compress/decompress
  round-trip including unicode.
- `test_notes_repository.py` (17) — full CRUD against fake CouchDB plus
  end-to-end HKDF round-trip (asserts chunk data on disk is genuinely
  encrypted) and a "passphrase without salt blocks writes" guard.
- `test_tools.py` (8) — MCP adapter layer: Pydantic conversion, ISO
  timestamps, folder normalization, semantic_search empty + honest
  coverage.
- `test_smoke.py` (2) — package imports.

## What's NOT done (next-PR candidates)

### High-priority
- **Real-vault integration tests.** Set up a Docker CouchDB + Obsidian
  with LiveSync, do round-trip writes from both sides, and verify the
  plugin can read what we wrote (and vice versa). Until this is done,
  every "Known gap" in DESIGN.md is unverified.
- **Semantic search implementation.** Needs:
  - Vector store choice (DESIGN.md open question — sqlite-vec / Chroma
    / Qdrant). Default to embedded.
  - Embedding backend (OpenAI default? require explicit config).
  - `_changes` subscriber in `vector/indexer.py` to keep index fresh.
  - Persistent watermark of the last-seen sequence so we resume cleanly.

### Medium-priority
- **Encryption interop validation.** HKDF V2 (`%=`) is implemented and
  unit-tested, but we still need a real round-trip against a vault
  encrypted by the plugin: write from Python → read in Obsidian, and
  vice versa. The spec is mirrored from
  `/tmp/octagonal-wheels/src/encryption/hkdf.ts`; risk is parameter
  drift, not the crypto itself.
- **Docker image.** A Dockerfile + GHA release workflow.
- **Splitter parity.** Our line-aware splitter doesn't match
  `ContentSplitterV2`'s output (lives in `livesync-commonlib` —
  `src/lib/src/string_and_binary/chunks.ts`). Content round-trips fine,
  but chunks won't dedup with plugin-written notes. Port
  `splitPieces2V2` faithfully.

### Lower-priority
- **Legacy "%" (PBKDF2) and "%~" (V3) encryption read support.**
  Currently raise `EncryptionNotSupportedError`. Skip unless someone
  files an issue with an older vault.
- **Local read cache** (DESIGN.md "Deferred / backburner").

## Open design questions still on the table

- Which vector store to default to (DESIGN.md "Open questions").
- Embedding backend default (probably ship without one and require
  explicit config).
- Chunking granularity for embeddings (per-heading? per-paragraph?).

## How to verify everything still works

```bash
cd mcp_server
uv sync
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

All should pass cleanly. CI runs the same checks on push.

## Git state

- Branch: `claude/setup-mcp-server-ifj5i`
- Latest commits push the MVP, tests, and this handoff. Nothing
  uncommitted at the time of writing.
- No PR opened — you control when to merge / open one.

## Gotchas to know about

- **CWD discipline**: ruff format from a wide directory will reformat
  unrelated files (the `.ipynb` at the repo root got reformatted once
  in this session — reverted in a follow-up commit). Always run ruff
  from `mcp_server/` or pass explicit paths.
- **xxhash dependency**: added to `dependencies` in pyproject.toml.
  Run `uv sync` after pulling to install it.
- **The skeleton's `Note` dataclass** (in `livesync/notes.py`) is
  separate from the `NoteModel` Pydantic class (in `tools/models.py`).
  The dataclass is the internal representation (unix-ms timestamps);
  the Pydantic model is what the LLM sees (ISO 8601 datetimes).
  `NoteModel.from_repo_note(note)` converts between them.
