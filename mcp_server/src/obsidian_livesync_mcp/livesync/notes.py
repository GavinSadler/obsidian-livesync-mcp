"""High-level note operations: read/write/delete notes by path.

Combines path encoding, chunk fetching, decryption, decompression,
and the inverse for writes.
"""

from __future__ import annotations

import base64
import json
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
from .chunks import (
    hash_chunk,
    split_content,
    split_pieces_rabin_karp,
)
from .chunks import (
    hashed_passphrase as compute_hashed_passphrase,
)
from .paths import path_to_id

WRITE_RETRY_LIMIT = 3
MAX_READ_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB safety net

SYNC_PARAMETERS_DOC_ID = "_local/obsidian_livesync_sync_parameters"

# When Path Obfuscation is enabled the plugin can no longer keep the real path
# in the (now hashed) document ID, so it stores the note's metadata — real
# ``path``, ``mtime``, ``ctime``, ``size`` and the chunk ``children`` list — as
# an encrypted JSON blob in the ``path`` field, prefixed with this marker and
# followed by an HKDF (``%=``) ciphertext. The top-level fields are zeroed. We
# call this "Property Encryption"; it rides along with obfuscation and uses the
# same passphrase/salt as content E2EE.
PROPERTY_ENC_PATH_PREFIX = "/\\:"


async def fetch_pbkdf2_salt(couch: CouchDBClient) -> bytes | None:
    """Read the vault's PBKDF2 salt from CouchDB.

    The LiveSync plugin stores it in a local doc at
    ``_local/obsidian_livesync_sync_parameters``, in the ``pbkdf2salt``
    field, base64-encoded. Returns ``None`` if the doc or field is
    missing (the vault has never been opened with E2EE enabled on the
    plugin side).
    """
    doc = await couch.get(SYNC_PARAMETERS_DOC_ID)
    if doc is None:
        return None
    encoded = doc.get("pbkdf2salt")
    if not isinstance(encoded, str) or not encoded:
        return None
    return base64.b64decode(encoded)


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


def _decode_chunk_payload(
    payload: str, *, passphrase: str | None, pbkdf2_salt: bytes | None
) -> str:
    """Decrypt (if needed) then decompress a chunk's ``data`` field.

    Order matters: the plugin compresses *before* encrypting, so on read
    we decrypt first and then decompress the resulting cleartext.
    """
    if encryption.is_hkdf_encrypted(payload):
        if not passphrase:
            raise EncryptedVaultError(
                "chunk is HKDF-encrypted but no LIVESYNC_PASSPHRASE is configured"
            )
        if payload.startswith(encryption.HKDF_PREFIX):
            if pbkdf2_salt is None:
                raise EncryptedVaultError(
                    "chunk is HKDF-encrypted but the vault PBKDF2 salt is not loaded; "
                    "check that the sync-parameters doc is reachable"
                )
            payload = encryption.decrypt_hkdf(payload, passphrase, pbkdf2_salt)
        else:
            # %$ ephemeral-salt format — salt travels with the ciphertext
            payload = encryption.decrypt_ephemeral_hkdf(payload, passphrase)
    elif encryption.is_encrypted(payload):
        # Legacy V2 (%) or V3 (%~) — not implemented
        raise EncryptedVaultError(
            "chunk uses an unsupported legacy encryption format "
            "(only HKDF / %= and %$ are implemented)"
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
        pbkdf2_salt: bytes | None = None,
        obfuscate_paths: bool = False,
        case_sensitive: bool = False,
        chunk_splitter: str = "v3",
    ) -> None:
        self._couch = couch
        self._passphrase = passphrase
        self._pbkdf2_salt = pbkdf2_salt
        # Per-vault salt mixed into encrypted chunk IDs (None when E2EE is off).
        self._hashed_passphrase = compute_hashed_passphrase(passphrase) if passphrase else None
        self._obfuscate = obfuscate_paths
        self._case_sensitive = case_sensitive
        if chunk_splitter not in ("v2", "v3"):
            raise ValueError(f"unknown chunk_splitter {chunk_splitter!r} (expected 'v2' or 'v3')")
        self._chunk_splitter = chunk_splitter
        if obfuscate_paths and not passphrase:
            raise ValueError("obfuscated paths require a passphrase")
        if passphrase and pbkdf2_salt is None:
            # Allowed for read-only/test cases, but we'll fail loud the
            # first time we try to write an encrypted chunk.
            pass

    def _path_to_id(self, path: str) -> str:
        return path_to_id(
            path,
            obfuscate=self._obfuscate,
            passphrase=self._passphrase,
            case_sensitive=self._case_sensitive,
        )

    @staticmethod
    def _is_property_encrypted(doc: dict[str, Any]) -> bool:
        """True if a note doc's metadata (path/children/…) is an encrypted blob.

        Detected by the ``/\\:`` marker plus an embedded HKDF (``%=``) ciphertext
        in the ``path`` field. See :data:`PROPERTY_ENC_PATH_PREFIX`.
        """
        path = doc.get("path")
        return isinstance(path, str) and path.startswith(PROPERTY_ENC_PATH_PREFIX) and "%=" in path

    def _decrypt_metadata(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a Property-Encrypted note doc's metadata blob into a dict.

        Returns the decrypted ``{path, mtime, ctime, size, children}`` mapping.
        Requires a configured passphrase and the vault PBKDF2 salt.
        """
        raw = str(doc["path"])
        marker = raw.index("%=")
        decoded = encryption.decrypt(raw[marker:], self._passphrase or "", self._pbkdf2_salt)
        meta: dict[str, Any] = json.loads(decoded)
        return meta

    def _effective_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Return a note doc with Property-Encrypted metadata resolved.

        For an ordinary (plain or content-only-E2EE) doc this is a no-op. For a
        Property-Encrypted doc it overlays the decrypted real ``path``,
        ``mtime``, ``ctime``, ``size`` and ``children`` onto the zeroed
        top-level fields so the rest of the repository can treat it uniformly.
        """
        if not self._is_property_encrypted(doc):
            return doc
        if not self._passphrase or self._pbkdf2_salt is None:
            raise EncryptedVaultError(
                "note metadata is Property-Encrypted (path obfuscation + E2EE) but no "
                "passphrase/salt is loaded; set LIVESYNC_PASSPHRASE and ensure the "
                "sync-parameters doc is reachable"
            )
        return {**doc, **self._decrypt_metadata(doc)}

    def effective_path(self, doc: dict[str, Any]) -> str | None:
        """Real note path for a raw doc (decrypting metadata if needed).

        Used by the _changes subscriber, where docs arrive straight off the
        feed and may carry Property-Encrypted metadata. Returns ``None`` if the
        doc has no usable path or the blob can't be decrypted.
        """
        try:
            resolved = self._effective_doc(doc)
        except (EncryptedVaultError, ValueError, KeyError):
            return None
        path = resolved.get("path")
        return path if isinstance(path, str) and path else None

    def set_pbkdf2_salt(self, salt: bytes) -> None:
        """Inject the vault PBKDF2 salt fetched at startup."""
        self._pbkdf2_salt = salt

    @property
    def needs_encryption(self) -> bool:
        """True iff a passphrase is configured (server should fetch the salt)."""
        return bool(self._passphrase)

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
            # Resolve Property-Encrypted metadata so the real path/mtime/size
            # surface instead of the encrypted blob and zeroed fields.
            doc = self._effective_doc(doc)
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
        # Resolve Property-Encrypted metadata (real path/children/mtime) so the
        # caller sees a uniform doc regardless of obfuscation.
        return self._effective_doc(doc)

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
            decoded = _decode_chunk_payload(
                data, passphrase=self._passphrase, pbkdf2_salt=self._pbkdf2_salt
            )
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
        """Split + hash + (optionally) compress + encrypt. Returns (children_ids, chunk_docs)."""
        encrypting = bool(self._passphrase)
        if encrypting and self._pbkdf2_salt is None:
            raise EncryptedVaultError(
                "LIVESYNC_PASSPHRASE is set but the vault PBKDF2 salt is not loaded; "
                "cannot write encrypted chunks"
            )
        if self._chunk_splitter == "v3":
            pieces = split_pieces_rabin_karp(content)
        else:
            pieces = split_content(content)
        children: list[str] = []
        docs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for piece in pieces:
            payload = encryption.compress(piece) if len(piece) > 64 else piece
            if encrypting:
                # Cast satisfies mypy; the None case is guarded above.
                assert self._pbkdf2_salt is not None
                payload = encryption.encrypt_hkdf(
                    payload, self._passphrase or "", self._pbkdf2_salt
                )
                chunk_id = hash_chunk(
                    piece, encrypted=True, hashed_passphrase=self._hashed_passphrase
                )
            else:
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
