"""Tests for content chunking and assembly (V2 algorithm)."""

from __future__ import annotations

from obsidian_livesync_mcp.livesync.chunks import (
    DEFAULT_MINIMUM_CHUNK_SIZE,
    DEFAULT_PIECE_SIZE,
    MAX_ITEMS,
    assemble_chunks,
    hash_chunk,
    split_content,
)


def test_assemble_chunks_empty() -> None:
    assert assemble_chunks([]) == ""


def test_assemble_chunks_concatenates_in_order() -> None:
    assert assemble_chunks(["hello, ", "world", "!"]) == "hello, world!"


def test_split_empty_returns_no_chunks() -> None:
    assert split_content("") == []


def test_split_then_assemble_is_identity(sample_note_content: str) -> None:
    chunks = split_content(sample_note_content)
    assert assemble_chunks(chunks) == sample_note_content


def test_split_emits_at_least_one_chunk_for_nonempty_input() -> None:
    chunks = split_content("hello")
    assert chunks
    assert chunks[0] == "hello"


def test_split_buffers_lines_until_min_chunk_size() -> None:
    # Short lines below the min-chunk threshold accumulate into a single
    # chunk rather than emitting one chunk per line.
    body = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"  # 20 chars total
    chunks = split_content(body, minimum_chunk_size=10)
    # Buffer reaches >10 chars then emits, then runs out and emits a tail.
    # Either way, fewer chunks than lines:
    assert len(chunks) < body.count("\n")
    assert assemble_chunks(chunks) == body


def test_split_round_trip_with_unicode(sample_note_content: str) -> None:
    body = sample_note_content + "Hello 世界 🌍\n" * 50
    chunks = split_content(body)
    assert assemble_chunks(chunks) == body


def test_split_caps_chunk_count_via_dynamic_min_size() -> None:
    # 5000 short lines would otherwise produce ~5000 chunks if every
    # newline emitted. The dynamic min-chunk-size scaling caps it.
    body = "x\n" * 5000  # 10000 chars
    chunks = split_content(body, minimum_chunk_size=DEFAULT_MINIMUM_CHUNK_SIZE)
    # MAX_ITEMS = 100; the scaling logic gives a soft cap on chunk count
    # (a few more are allowed for tail/overflow).
    assert len(chunks) <= MAX_ITEMS + 10
    assert assemble_chunks(chunks) == body


def test_split_slices_long_single_piece_at_piece_size() -> None:
    # Whole input is one logical "line" longer than piece_size; force
    # the chunk_string_generator path.
    body = "x" * (DEFAULT_PIECE_SIZE * 2 + 17)
    chunks = split_content(body)
    assert all(len(c) <= DEFAULT_PIECE_SIZE for c in chunks)
    assert assemble_chunks(chunks) == body


def test_split_plain_split_false_uses_fixed_slicing() -> None:
    # plain_split=False bypasses the line-aware path entirely.
    body = "abcdef\nghijkl\nmnopqr\n"
    chunks = split_content(body, piece_size=5, plain_split=False)
    assert all(len(c) <= 5 for c in chunks)
    assert assemble_chunks(chunks) == body


def test_split_max_chunk_size_alias_still_accepted() -> None:
    # The old kwarg name maps to piece_size for back-compat.
    body = "line one\nline two\nline three\n"
    chunks = split_content(body, max_chunk_size=10)
    assert all(len(c) <= 10 for c in chunks)
    assert assemble_chunks(chunks) == body


def test_hash_chunk_is_stable() -> None:
    assert hash_chunk("hello") == hash_chunk("hello")
    assert hash_chunk("hello").startswith("h:")


def test_hash_chunk_distinguishes_content() -> None:
    assert hash_chunk("a") != hash_chunk("b")


def test_hash_chunk_encrypted_marker() -> None:
    assert hash_chunk("hello", encrypted=True).startswith("h:+")
    assert not hash_chunk("hello", encrypted=False).startswith("h:+")


# Reference outputs captured from a verbatim port of splitPieces2V2's text
# path running in Node (livesync-commonlib/src/string_and_binary/chunks.ts).
# These pin byte-for-byte parity so chunk dedup with plugin-written notes
# keeps working. Regenerate via:
#     echo "<text>" | node scripts/splitref.mjs <piece_size> <min_chunk_size>
# if the algorithm in the plugin changes.
_PARITY_FIXTURES: list[tuple[str, str, int, int, list[str]]] = [
    (
        "typical markdown",
        "# Heading\npara one\npara two\n",
        102400,
        20,
        ["# Heading\npara one\npara two\n", ""],
    ),
    (
        "trailing newline emits empty chunk",
        "abcdef\nghijkl\nmnopqr\n",
        5,
        3,
        ["abcde", "f\n", "ghijk", "l\n", "mnopq", "r\n", ""],
    ),
    (
        "supplementary chars use UTF-16 length",
        "Hi 🌍\n" * 6,
        102400,
        20,
        ["Hi 🌍\nHi 🌍\nHi 🌍\nHi 🌍\n", "Hi 🌍\nHi 🌍\n"],
    ),
]


def test_parity_with_plugin_reference() -> None:
    for name, text, piece_size, min_chunk, expected in _PARITY_FIXTURES:
        got = split_content(text, piece_size=piece_size, minimum_chunk_size=min_chunk)
        assert got == expected, f"{name}: got {got!r}, expected {expected!r}"
