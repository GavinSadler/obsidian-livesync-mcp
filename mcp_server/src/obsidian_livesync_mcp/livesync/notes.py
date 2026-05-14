"""High-level note operations: read/write/delete notes by path.

Combines path encoding, chunk fetching, decryption, decompression,
and the inverse for writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..couchdb import CouchDBClient


@dataclass(frozen=True)
class Note:
    path: str
    content: str
    ctime: int
    mtime: int
    size: int


class NoteRepository:
    """Read/write notes against a LiveSync CouchDB database."""

    def __init__(
        self,
        couch: CouchDBClient,
        *,
        passphrase: str | None = None,
        obfuscate_paths: bool = False,
    ) -> None:
        raise NotImplementedError

    async def list_paths(
        self,
        path_prefix: str | None = None,
        limit: int | None = None,
    ) -> list[str]:
        raise NotImplementedError

    async def read(self, path: str) -> Note | None:
        raise NotImplementedError

    async def create(self, path: str, content: str) -> Note:
        raise NotImplementedError

    async def update(self, path: str, content: str) -> Note:
        raise NotImplementedError

    async def delete(self, path: str) -> bool:
        raise NotImplementedError

    async def move(self, old_path: str, new_path: str) -> Note:
        """Move/rename a note. Implementation may be read + create + delete
        under the hood; the public contract is atomic-on-success."""
        raise NotImplementedError
