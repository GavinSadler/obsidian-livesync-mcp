"""High-level note operations: read/write/delete notes by path.

Combines path encoding, chunk fetching, decryption, decompression,
and the inverse for writes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from ..couchdb import CouchDBClient
from ..errors import (
    CouchDBError,
    EncryptedVaultError,
    InvalidPathError,
    MovePartialFailureError,
    NoteAlreadyExistsError,
    NoteNotFoundError,
    NoteWriteError,
)
from . import encryption
from .chunks import hash_chunk, split_content
from .paths import path_to_id

WRITE_RETRY_LIMIT = 3
MAX_READ_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB safety net

# Forbid characters Obsidian itself rejects (Windows-incompatible).
_FORBIDDEN_CHARS = re.compile(r'[\\:\*\?"<>|]')


@dataclass(frozen=True)
class Note:
    path: str
    content: str
    ctime: int  # unix ms
    mtime: int  # unix ms
    size: int


def _validate_path(path: str, *, require_md: bool = False) -> None:
    if not path:
        raise InvalidPathError("path must be non-empty")
    if path.startswith("/"):
        raise InvalidPathError("path must not start with '/'")
    if ".." in path.split("/"):
        raise InvalidPathError("path must not contain '..'")
    if _FORBIDDEN_CHARS.search(path):
        raise InvalidPathError(f"path contains forbidden characters: {path!r}")
    if require_md and not path.endswith(".md"):
        raise InvalidPathError(f"path must end in '.md': {path!r}")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decode_chunk_payload(payload: str) -> str:
    """Decompress + decrypt a chunk's `data` field into plaintext."""
    if encryption.is_encrypted(payload):
        raise EncryptedVaultError(
            "vault contains encrypted chunks; the MVP does not support encryption. "
            "Disable End-to-End Encryption in the LiveSync plugin settings."
        )
    if encryption.is_compressed(payload):
        return encryption.decompress(payload)
    return payload


class NoteRepository:
    """Read/write notes against a LiveSync CouchDB database."""

    def __init__(
        self,
        couch: CouchDBClient,
        *,
        passphrase: str | None = None,
        obfuscate_paths: bool = False,
    ) -> None:
        self._couch = couch
        self._passphrase = passphrase
        self._obfuscate = obfuscate_paths
        if obfuscate_paths and not passphrase:
            raise ValueError("obfuscated paths require a passphrase")

    def _path_to_id(self, path: str) -> str:
        return path_to_id(path, obfuscate=self._obfuscate, passphrase=self._passphrase)

    async def list_paths(
        self,
        path_prefix: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return note summaries: [{path, mtime, size}, ...].

        Filters out chunk docs (`h:` prefix), CouchDB internal docs
        (`_design/`, `_local/`), and soft-deleted notes. Sorted by path.
        """
        # Plain mode lets us range-query directly by prefix. Obfuscated
        # mode requires fetching all docs and filtering by the `path` field.
        if path_prefix and not self._obfuscate:
            start: str | None = path_prefix
            # CouchDB key ordering: ￿ is the canonical "highest" character.
            end: str | None = f"{path_prefix}￿"
        else:
            start, end = None, None

        rows = await self._couch.all_docs(
            start_key=start,
            end_key=end,
            include_docs=True,
            limit=None,
        )

        results: list[dict[str, Any]] = []
        for row in rows:
            doc = row.get("doc")
            if not doc:
                continue
            doc_id = doc.get("_id", "")
            if doc_id.startswith(("_", "h:")):
                continue
            if doc.get("deleted") is True:
                continue
            doc_type = doc.get("type")
            if doc_type not in ("plain", "newnote"):
                continue
            path = doc.get("path", doc_id)
            if path_prefix and not path.startswith(path_prefix):
                continue
            results.append(
                {
                    "path": path,
                    "mtime": int(doc.get("mtime", 0) or 0),
                    "size": int(doc.get("size", 0) or 0),
                }
            )

        results.sort(key=lambda r: str(r["path"]))
        if limit is not None:
            return results[:limit]
        return results

    async def _get_note_doc(self, path: str) -> dict[str, Any] | None:
        """Fetch the parent note doc, returning None on 404 or soft-delete."""
        doc_id = self._path_to_id(path)
        doc = await self._couch.get(doc_id)
        if doc is None:
            return None
        if doc.get("deleted") is True:
            return None
        return doc

    async def _assemble_content(self, note_doc: dict[str, Any]) -> str:
        """Fetch and assemble chunks for a note doc into plaintext."""
        children_raw = note_doc.get("children") or []
        if not isinstance(children_raw, list):
            return ""
        children: list[str] = [str(c) for c in children_raw]
        if not children:
            return ""
        chunk_docs = await self._couch.bulk_get(children)
        parts: list[str] = []
        total = 0
        for cid, chunk in zip(children, chunk_docs, strict=True):
            if chunk is None:
                raise NoteNotFoundError(f"missing chunk {cid!r} for note")
            data = chunk.get("data", "")
            if not isinstance(data, str):
                raise NoteWriteError(f"chunk {cid!r} has non-string data field")
            decoded = _decode_chunk_payload(data)
            total += len(decoded.encode("utf-8"))
            if total > MAX_READ_SIZE_BYTES:
                raise NoteWriteError(
                    f"note exceeds {MAX_READ_SIZE_BYTES} byte safety cap; refusing to assemble"
                )
            parts.append(decoded)
        return "".join(parts)

    async def read(self, path: str) -> Note | None:
        _validate_path(path)
        doc = await self._get_note_doc(path)
        if doc is None:
            return None
        content = await self._assemble_content(doc)
        return Note(
            path=str(doc.get("path", path)),
            content=content,
            ctime=int(doc.get("ctime", 0) or 0),
            mtime=int(doc.get("mtime", 0) or 0),
            size=int(doc.get("size", len(content.encode("utf-8"))) or 0),
        )

    def _build_chunk_docs(self, content: str) -> tuple[list[str], list[dict[str, Any]]]:
        """Split + hash content. Returns (children_ids, chunk_docs_to_write)."""
        pieces = split_content(content)
        children: list[str] = []
        docs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for piece in pieces:
            # Compress chunks above a small threshold; tiny chunks aren't
            # worth the marker overhead.
            payload = encryption.compress(piece) if len(piece) > 64 else piece
            chunk_id = hash_chunk(piece)
            children.append(chunk_id)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            docs.append({"_id": chunk_id, "data": payload, "type": "leaf"})
        return children, docs

    async def _write_note(
        self,
        path: str,
        content: str,
        *,
        existing: dict[str, Any] | None,
        ctime: int,
        mtime: int,
    ) -> Note:
        """Write the chunks + parent doc with 409-retry semantics."""
        doc_id = self._path_to_id(path)
        children, chunk_docs = self._build_chunk_docs(content)
        size = len(content.encode("utf-8"))

        last_existing = existing
        for attempt in range(WRITE_RETRY_LIMIT):
            parent: dict[str, Any] = {
                "_id": doc_id,
                "path": path,
                "type": "plain",
                "ctime": ctime,
                "mtime": mtime,
                "size": size,
                "children": list(children),
                "eden": {},
            }
            if last_existing is not None and last_existing.get("_rev"):
                parent["_rev"] = last_existing["_rev"]

            try:
                if chunk_docs:
                    results = await self._couch.bulk_docs(chunk_docs)
                    for result in results:
                        err = result.get("error")
                        if err and err != "conflict":
                            raise NoteWriteError(f"chunk write failed: {result.get('reason')}")
                await self._couch.put(parent)
                return Note(
                    path=path,
                    content=content,
                    ctime=ctime,
                    mtime=mtime,
                    size=size,
                )
            except CouchDBError as e:
                if e.status_code == 409 and attempt < WRITE_RETRY_LIMIT - 1:
                    last_existing = await self._couch.get(doc_id)
                    continue
                raise NoteWriteError(f"write failed after {attempt + 1} attempts: {e}") from e

        raise NoteWriteError(f"write failed after {WRITE_RETRY_LIMIT} attempts")

    async def create(self, path: str, content: str) -> Note:
        _validate_path(path, require_md=True)
        doc_id = self._path_to_id(path)
        existing = await self._couch.get(doc_id)
        if existing is not None and existing.get("deleted") is not True:
            raise NoteAlreadyExistsError(f"note already exists: {path!r}")
        now = _now_ms()
        return await self._write_note(path, content, existing=existing, ctime=now, mtime=now)

    async def update(self, path: str, content: str) -> Note:
        _validate_path(path)
        existing = await self._get_note_doc(path)
        if existing is None:
            raise NoteNotFoundError(f"note does not exist: {path!r}")
        ctime = int(existing.get("ctime", _now_ms()) or _now_ms())
        return await self._write_note(
            path, content, existing=existing, ctime=ctime, mtime=_now_ms()
        )

    async def delete(self, path: str) -> bool:
        _validate_path(path)
        doc_id = self._path_to_id(path)
        existing = await self._couch.get(doc_id)
        if existing is None or existing.get("deleted") is True:
            raise NoteNotFoundError(f"note does not exist: {path!r}")

        for attempt in range(WRITE_RETRY_LIMIT):
            tombstone = dict(existing)
            tombstone["deleted"] = True
            tombstone["mtime"] = _now_ms()
            try:
                await self._couch.put(tombstone)
                return True
            except CouchDBError as e:
                if e.status_code == 409 and attempt < WRITE_RETRY_LIMIT - 1:
                    refetched = await self._couch.get(doc_id)
                    if refetched is None or refetched.get("deleted") is True:
                        raise NoteNotFoundError(f"note does not exist: {path!r}") from e
                    existing = refetched
                    continue
                raise NoteWriteError(f"delete failed: {e}") from e

        raise NoteWriteError(f"delete failed after {WRITE_RETRY_LIMIT} attempts")

    async def move(self, old_path: str, new_path: str) -> Note:
        """Move/rename a note. Create-at-new then delete-at-old.

        On partial failure (created but not deleted), raises
        MovePartialFailureError so the caller can clean up.
        """
        _validate_path(old_path)
        _validate_path(new_path, require_md=True)
        if old_path == new_path:
            raise InvalidPathError("old_path and new_path must differ")

        source = await self.read(old_path)
        if source is None:
            raise NoteNotFoundError(f"note does not exist: {old_path!r}")

        new_note = await self.create(new_path, source.content)

        try:
            await self.delete(old_path)
        except Exception as e:
            raise MovePartialFailureError(old_path, new_path, str(e)) from e

        return new_note
