"""Path ↔ document ID conversion.

Plain mode: ID is essentially the path (with a leading-underscore guard).
Obfuscated mode: ID is `f:` + hex(SHA-256(stretched_passphrase + ":" + path)).

Reference: src/lib/src/string_and_binary/path.ts (path2id_base, id2path_base).
"""

from __future__ import annotations


def path_to_id(path: str, *, obfuscate: bool = False, passphrase: str | None = None) -> str:
    """Convert an Obsidian file path to a LiveSync document ID."""
    raise NotImplementedError


def id_to_path(doc_id: str, *, fallback_path: str | None = None) -> str:
    """Convert a document ID back to a file path.

    For obfuscated IDs the path can't be recovered from the ID alone — pass
    the document's `path` field as `fallback_path`.
    """
    raise NotImplementedError


def is_chunk_id(doc_id: str) -> bool:
    """Return True for `h:` / `h:+` chunk IDs."""
    return doc_id.startswith("h:")


def is_obfuscated_id(doc_id: str) -> bool:
    return doc_id.startswith("f:")
