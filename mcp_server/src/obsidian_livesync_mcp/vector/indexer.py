"""Background indexer: subscribes to CouchDB _changes and updates the
vector store as notes are added, updated, or deleted.

Lifecycle:
  1. On startup, restore last-seen sequence from a small state doc.
  2. If no state (or `--rebuild` was passed), backfill: list all notes and
     index them from scratch.
  3. Subscribe to `_changes?feed=continuous&since=<seq>&include_docs=true`.
  4. For each change row:
       - chunk change → ignore (we operate on note documents only).
       - note doc with deleted=true → store.delete_by_note(path).
       - note doc otherwise → fetch full content via NoteRepository, chunk
         for embedding, embed, upsert.
  5. Periodically persist the latest seq.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..couchdb import CouchDBClient
from ..livesync.notes import NoteRepository
from .embeddings import EmbeddingBackend
from .store import VectorStore


class Indexer:
    def __init__(
        self,
        couch: CouchDBClient,
        repo: NoteRepository,
        store: VectorStore,
        embedder: EmbeddingBackend,
    ) -> None:
        raise NotImplementedError

    async def run(self) -> None:
        """Run the indexer loop until cancelled."""
        raise NotImplementedError

    async def backfill(self) -> None:
        """Index every note in the vault from scratch."""
        raise NotImplementedError

    async def reindex_note(self, path: str) -> None:
        raise NotImplementedError

    @staticmethod
    def chunk_for_embedding(content: str) -> Iterable[tuple[int, str | None, str]]:
        """Split note content into embedding-sized pieces.

        Yields (chunk_index, nearest_heading, text). Default policy:
        split on markdown headings, then fall back to fixed-size windows
        for long sections.
        """
        raise NotImplementedError
