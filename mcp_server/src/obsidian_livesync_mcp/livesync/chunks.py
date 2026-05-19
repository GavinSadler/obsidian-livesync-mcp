"""Content splitting and chunk hashing.

LiveSync splits file content into chunks and stores each chunk as its
own CouchDB document keyed by a content hash. The parent note document
carries a ``children`` array of these chunk IDs.

Splitter is a port of ``splitPieces2V2`` (text path,
``plainSplit=true``, ``useSegmenter=false``) from
``livesync-commonlib/src/string_and_binary/chunks.ts``:

  1. Walk the input character by character. Cap the minimum chunk size
     by scaling it up until ``len(text) / min_chunk_size <= MAX_ITEMS``.
     This keeps very large notes from producing thousands of tiny chunks.
  2. Accumulate on ``\\n`` boundaries until the buffer exceeds the
     scaled minimum chunk size, then emit.
  3. If any emitted piece exceeds ``piece_size``, further slice it on
     character boundaries (rare for normal markdown).

Length parity: chunk-size checks count UTF-16 code units (matching the
JS plugin), not Python code points. A character above U+FFFF (e.g.
emoji) counts as 2 — same as ``"x".length`` in JavaScript. This is
required for chunk-dedup parity with the plugin on inputs that contain
supplementary-plane characters.

Reference:
  - src/lib/src/string_and_binary/chunks.ts (splitPieces2V2 + helpers)
  - src/lib/src/ContentSplitter/ContentSplitterBase.ts (default params)
  - src/lib/src/common/models/shared.const.behabiour.ts (size constants)
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import xxhash

# Plugin defaults (src/lib/src/common/models/shared.const.behabiour.ts +
# setting.const.defaults.ts).
MAX_DOC_SIZE_BIN = 102_400  # bytes; the plugin's pieceSize for text+binary
DEFAULT_PIECE_SIZE = MAX_DOC_SIZE_BIN
DEFAULT_MINIMUM_CHUNK_SIZE = 20
MAX_ITEMS = 100  # cap on chunk count via dynamic min-size scaling

# Back-compat alias for tests / callers that referenced the old constant.
MAX_DOC_SIZE_TEXT = MAX_DOC_SIZE_BIN

PREFIX_CHUNK = "h:"
PREFIX_ENCRYPTED_CHUNK = "h:+"


def _utf16_len(s: str) -> int:
    """Number of UTF-16 code units, matching JS ``string.length``.

    Each code point ≤ U+FFFF counts as 1; supplementary code points
    (> U+FFFF, e.g. most emoji) count as 2.
    """
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def _to_base36(n: int) -> str:
    """Mirror JavaScript's ``Number.prototype.toString(36)`` (lowercase)."""
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out: list[str] = []
    while n > 0:
        out.append(digits[n % 36])
        n //= 36
    return "".join(reversed(out))


def _chunk_string_generator(source: str, max_length: int) -> Iterator[str]:
    """Slice a string into pieces of at most ``max_length`` UTF-16 code units.

    Mirrors ``chunkStringGenerator`` in the TS source. Python ``str`` is
    indexed by code point (atomic), so we walk code-point-by-code-point
    and emit when the cumulative UTF-16 length reaches ``max_length``.
    A supplementary code point at the boundary takes both UTF-16 units
    into the same chunk — equivalent to the JS surrogate-pair fix-up
    (the ``end++`` to keep the low surrogate with its high surrogate).
    """
    if _utf16_len(source) <= max_length:
        yield source
        return
    buf: list[str] = []
    cur = 0
    for c in source:
        cur += 2 if ord(c) > 0xFFFF else 1
        buf.append(c)
        if cur >= max_length:
            yield "".join(buf)
            buf = []
            cur = 0
    if buf:
        yield "".join(buf)


def _split_by_delimiter_with_min_length(
    sources: Iterable[str],
    delimiter: str,
    minimum_chunk_length: int = 25,
    split_threshold: int | None = None,
) -> Iterator[str]:
    """Concatenate sources, split on ``delimiter``, accumulate to a min length.

    Port of ``splitByDelimiterWithMinLength``. Keeps appending segments
    (including the delimiter) onto a running buffer; emits the buffer
    once its UTF-16 length exceeds ``minimum_chunk_length``.
    ``split_threshold`` lets large single sources bypass the buffer
    entirely (used in the binary path; unused for text).

    Length comparisons use UTF-16 code units to match JS ``string.length``,
    so chunk boundaries line up with the plugin's even when supplementary
    code points (emoji etc.) are involved.

    Note: when a source ends exactly at a delimiter, this yields a final
    empty-string chunk — matching the JS reference. ``assemble_chunks``
    concatenates them harmlessly.
    """
    buf = ""
    last = False
    dl = len(delimiter)
    for source in sources:
        if split_threshold is not None and _utf16_len(source) > split_threshold:
            yield buf + source
            last = False
            buf = ""
            continue
        prev = 0
        while True:
            i = source.find(delimiter, prev)
            if i == -1:
                # No more delimiters: pick up the tail (may be ""), set
                # last=True, and roll into the next source. Matches the
                # TS post-loop branch.
                buf += source[prev:]
                last = True
                break
            buf += source[prev : i + dl]
            if _utf16_len(buf) > minimum_chunk_length:
                yield buf
                buf = ""
                last = False
            else:
                last = True
            prev = i + dl
    if last:
        yield buf


def split_content(
    content: str,
    *,
    piece_size: int = DEFAULT_PIECE_SIZE,
    minimum_chunk_size: int = DEFAULT_MINIMUM_CHUNK_SIZE,
    plain_split: bool = True,
    max_chunk_size: int | None = None,
) -> list[str]:
    """Split note content using the V2 algorithm (``splitPieces2V2``, text path).

    ``piece_size`` caps the maximum bytes a single chunk can take (the
    plugin uses ``MAX_DOC_SIZE_BIN`` = 100 KiB). ``minimum_chunk_size``
    seeds the accumulation threshold and is scaled up automatically so
    no more than ~100 chunks come out of any one note.

    ``max_chunk_size`` is accepted for backwards compatibility and is
    treated as an alias for ``piece_size``.
    """
    if not content:
        return []
    if max_chunk_size is not None:
        piece_size = max_chunk_size

    if not plain_split:
        return list(_chunk_string_generator(content, piece_size))

    text_len = _utf16_len(content)
    x_min_chunk_size = minimum_chunk_size
    # Scale the minimum chunk size up so we don't emit more than
    # MAX_ITEMS chunks. Matches the loop in splitPieces2V2.
    while x_min_chunk_size > 0 and text_len / x_min_chunk_size > MAX_ITEMS:
        x_min_chunk_size += minimum_chunk_size

    result: list[str] = []
    for piece in _split_by_delimiter_with_min_length(
        [content], "\n", minimum_chunk_length=x_min_chunk_size
    ):
        result.extend(_chunk_string_generator(piece, piece_size))
    return result


def hash_chunk(chunk: str, *, encrypted: bool = False) -> str:
    """Return the ``h:`` (or ``h:+``) chunk document ID for a piece of content.

    Matches the plugin's XXHash64 path::

        xxhash.h64(f"{piece}-{piece.length}").toString(36)

    ``piece.length`` in JS is UTF-16 code-unit count; we use the same
    here so hashes match plugin-written chunks of identical content
    even when supplementary code points are present.
    """
    payload = f"{chunk}-{_utf16_len(chunk)}".encode()
    digest = xxhash.xxh64(payload).intdigest()
    hash_str = _to_base36(digest)
    prefix = PREFIX_ENCRYPTED_CHUNK if encrypted else PREFIX_CHUNK
    return prefix + hash_str


def assemble_chunks(chunk_data: list[str]) -> str:
    """Reverse of split: concatenate ordered chunk payloads into one string."""
    return "".join(chunk_data)
