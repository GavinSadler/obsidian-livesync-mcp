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

import xxhash

MAX_DOC_SIZE_TEXT = 1000  # chars
MAX_DOC_SIZE_BINARY = 102_400  # bytes

PREFIX_CHUNK = "h:"
PREFIX_ENCRYPTED_CHUNK = "h:+"


def _to_base36(n: int) -> str:
    """Mirror JavaScript's `Number.prototype.toString(36)` (lowercase)."""
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while n > 0:
        out.append(digits[n % 36])
        n //= 36
    return "".join(reversed(out))


def split_content(content: str, *, max_chunk_size: int = MAX_DOC_SIZE_TEXT) -> list[str]:
    """Split note content into chunks matching LiveSync's splitter behavior.

    Simplified line-aware splitter: emit chunks whose total length stays
    at or below `max_chunk_size`, breaking on newlines where possible.
    A single line longer than `max_chunk_size` is hard-split at the limit.

    NOTE: The reference splitter (ContentSplitterV2 + Intl.Segmenter sentence
    segmentation) is more sophisticated. Our chunks will differ from the
    plugin's for the same input, which means:
      - Content round-trips correctly (we can read what we write).
      - Chunk IDs won't match the plugin's for identical content, so
        chunk dedup with plugin-written notes won't happen.
    Tracked in DESIGN.md "Known gaps" — needs the V2 splitter for real
    compatibility.
    """
    if not content:
        return []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for line in content.splitlines(keepends=True):
        if len(line) > max_chunk_size:
            if buf:
                chunks.append("".join(buf))
                buf, buf_len = [], 0
            for i in range(0, len(line), max_chunk_size):
                chunks.append(line[i : i + max_chunk_size])
            continue

        if buf_len + len(line) > max_chunk_size:
            chunks.append("".join(buf))
            buf, buf_len = [], 0

        buf.append(line)
        buf_len += len(line)

    if buf:
        chunks.append("".join(buf))

    return chunks


def hash_chunk(chunk: str, *, encrypted: bool = False) -> str:
    """Return the `h:` (or `h:+`) chunk document ID for a piece of content.

    Matches the plugin's XXHash64 path:
        xxhash.h64(`${piece}-${piece.length}`).toString(36)
    where `piece.length` is the JS string-length (UTF-16 code units).
    We approximate that as the Python `len()` of the string, which matches
    for the BMP. Strings with characters outside the BMP (rare in notes)
    will differ — flagged in DESIGN.md.
    """
    payload = f"{chunk}-{len(chunk)}".encode()
    digest = xxhash.xxh64(payload).intdigest()
    hash_str = _to_base36(digest)
    prefix = PREFIX_ENCRYPTED_CHUNK if encrypted else PREFIX_CHUNK
    return prefix + hash_str


def assemble_chunks(chunk_data: list[str]) -> str:
    """Reverse of split: concatenate ordered chunk payloads into one string."""
    return "".join(chunk_data)
