"""Path ↔ document ID conversion.

Plain mode: ID is essentially the path (lowercased unless case-sensitive).
Obfuscated mode: ID is ``f:`` + hex(SHA-256(``hashedPassphrase:lower(path)``)),
where ``hashedPassphrase`` is itself SHA-256(passphrase).

Reference: livesync-commonlib src/string_and_binary/path.ts (path2id_base).
"""

from __future__ import annotations

import hashlib

PREFIX_OBFUSCATED = "f:"
PREFIX_CHUNK = "h:"


def _hash_string(value: str) -> str:
    """Mirror LiveSync's ``hashString``: a single SHA-256 over UTF-8 bytes, hex.

    The plugin's ``_hashString`` has a "stretching" loop that re-hashes the
    *original* buffer every iteration instead of the running digest (an upstream
    bug in livesync-commonlib), so it collapses to one SHA-256. We replicate that
    exactly. Verified against a real obfuscated-vault export: reproduces all 38
    ``f:`` document IDs.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def path_to_id(
    path: str,
    *,
    obfuscate: bool = False,
    passphrase: str | None = None,
    case_sensitive: bool = False,
) -> str:
    """Convert an Obsidian file path to a LiveSync document ID.

    ``case_sensitive`` mirrors the plugin's "Handle files as Case-Sensitive"
    setting. The default (``False``) is the plugin's recommended default: the
    document ID is lowercased so the same note can't collide across
    case-insensitive filesystems (Windows/macOS/Android). The real filename
    case is preserved separately in the doc's ``path`` field, not the ID.

    Verified against a real exported vault: every note doc had
    ``_id == path.lower()`` in plain mode.
    """
    if not path:
        raise ValueError("path must be non-empty")

    if obfuscate:
        if not passphrase:
            raise ValueError("obfuscated mode requires a passphrase")
        # The plugin lowercases the filename before hashing when case-insensitive
        # (its default), but hashes the passphrase as-is. Verified against a real
        # obfuscated-vault export.
        filename = path if case_sensitive else path.lower()
        hashed_passphrase = _hash_string(passphrase)
        return PREFIX_OBFUSCATED + _hash_string(f"{hashed_passphrase}:{filename}")

    if not case_sensitive:
        path = path.lower()

    # Plain mode: prepend "/" if path begins with "_" to avoid colliding
    # with CouchDB reserved IDs (_design, _local, etc.).
    if path.startswith("_"):
        return "/" + path
    return path


def id_to_path(doc_id: str, *, fallback_path: str | None = None) -> str:
    """Convert a document ID back to a file path.

    For obfuscated IDs the path can't be recovered from the ID alone — pass
    the document's `path` field as `fallback_path`.
    """
    if doc_id.startswith(PREFIX_OBFUSCATED):
        if fallback_path is None:
            raise ValueError("obfuscated ID requires fallback_path (the doc's `path` field)")
        return fallback_path

    # Plain mode: strip the leading "/" we added in path_to_id.
    if doc_id.startswith("/_"):
        return doc_id[1:]
    return doc_id


def is_chunk_id(doc_id: str) -> bool:
    """Return True for `h:` / `h:+` chunk IDs."""
    return doc_id.startswith(PREFIX_CHUNK)


def is_obfuscated_id(doc_id: str) -> bool:
    return doc_id.startswith(PREFIX_OBFUSCATED)
