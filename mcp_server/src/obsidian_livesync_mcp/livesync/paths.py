"""Path ↔ document ID conversion.

Plain mode: ID is essentially the path (with a leading-underscore guard).
Obfuscated mode: ID is `f:` + hex(SHA-256(stretched_passphrase + ":" + path)).

Reference: src/lib/src/string_and_binary/path.ts (path2id_base, id2path_base).
"""

from __future__ import annotations

import hashlib

PREFIX_OBFUSCATED = "f:"
PREFIX_CHUNK = "h:"


def _stretch_hash(value: str) -> str:
    """Mirror the LiveSync passphrase stretching loop.

    Plugin calls SHA-256 in a loop `key.length` times, where `key.length`
    is the input string's character count. We do the same: start with the
    UTF-8 bytes, hash once per character, hex-encode the final digest.

    See DESIGN.md "Known gaps" — the precise semantics of `key.length`
    in the JS source are inferred and need real-vault verification.
    """
    data = value.encode("utf-8")
    for _ in range(len(value)):
        data = hashlib.sha256(data).digest()
    return data.hex()


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
        # NOTE: whether the plugin lowercases the path before obfuscation
        # hashing is not yet verified against an obfuscated-vault export, so
        # we intentionally do not apply case folding here yet.
        hashed_passphrase = _stretch_hash(passphrase)
        return PREFIX_OBFUSCATED + _stretch_hash(f"{hashed_passphrase}:{path}")

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
