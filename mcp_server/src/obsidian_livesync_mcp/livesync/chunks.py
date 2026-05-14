"""Content splitting and chunk hashing.

LiveSync splits file content into chunks (~1KB for text, ~100KB for binary)
and stores each chunk as its own CouchDB document keyed by a content hash.
The parent note document carries a `children` array of these chunk IDs.

Reference:
  - src/lib/src/ContentSplitter/  (splitting strategies)
  - src/lib/src/managers/EntryManager/EntryManagerImpls.ts  (assembly)
  - src/lib/src/common/models/shared.const.behabiour.ts  (size constants)
"""

from __future__ import annotations

MAX_DOC_SIZE_TEXT = 1000  # chars
MAX_DOC_SIZE_BINARY = 102_400  # bytes


def split_content(content: str, *, max_chunk_size: int = MAX_DOC_SIZE_TEXT) -> list[str]:
    """Split note content into chunks matching LiveSync's splitter behavior."""
    raise NotImplementedError


def hash_chunk(chunk: str, *, encrypted: bool = False) -> str:
    """Return the `h:` (or `h:+`) chunk document ID for a piece of content."""
    raise NotImplementedError


def assemble_chunks(chunk_data: list[str]) -> str:
    """Reverse of split: concatenate ordered chunk payloads into one string."""
    return "".join(chunk_data)
