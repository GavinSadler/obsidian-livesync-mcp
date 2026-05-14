"""Vector store abstraction.

The concrete backend (sqlite-vec, Chroma, Qdrant, ...) is chosen at runtime
based on configuration. All backends implement this Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorRecord:
    id: str  # stable id, e.g. "<note_path>#<chunk_index>"
    note_path: str
    chunk_index: int
    heading: str | None  # nearest markdown heading, if any
    content: str
    embedding: list[float]


@dataclass(frozen=True)
class SearchHit:
    record: VectorRecord
    score: float


class VectorStore(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None: ...

    async def delete_by_note(self, note_path: str) -> None: ...

    async def search(
        self,
        embedding: list[float],
        top_k: int,
        path_filter: str | None = None,
    ) -> list[SearchHit]: ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...
