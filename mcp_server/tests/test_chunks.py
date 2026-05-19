"""Tests for content chunking and assembly."""

from __future__ import annotations

from obsidian_livesync_mcp.livesync.chunks import (
    MAX_DOC_SIZE_TEXT,
    assemble_chunks,
    hash_chunk,
    split_content,
)


def test_assemble_chunks_empty() -> None:
    assert assemble_chunks([]) == ""


def test_assemble_chunks_concatenates_in_order() -> None:
    assert assemble_chunks(["hello, ", "world", "!"]) == "hello, world!"


def test_split_then_assemble_is_identity(sample_note_content: str) -> None:
    chunks = split_content(sample_note_content)
    assert assemble_chunks(chunks) == sample_note_content


def test_split_empty_returns_no_chunks() -> None:
    assert split_content("") == []


def test_split_respects_max_chunk_size() -> None:
    # Long content; each chunk should stay within the limit (with the
    # exception of single lines longer than the limit, which get hard-split).
    content = "line\n" * 500  # 2500 chars, ~5-char lines
    chunks = split_content(content, max_chunk_size=100)
    assert all(len(c) <= 100 for c in chunks)
    assert assemble_chunks(chunks) == content


def test_split_hard_splits_long_lines() -> None:
    big_line = "x" * (MAX_DOC_SIZE_TEXT * 3) + "\n"
    chunks = split_content(big_line)
    assert all(len(c) <= MAX_DOC_SIZE_TEXT for c in chunks)
    assert assemble_chunks(chunks) == big_line


def test_hash_chunk_is_stable() -> None:
    assert hash_chunk("hello") == hash_chunk("hello")
    assert hash_chunk("hello").startswith("h:")


def test_hash_chunk_distinguishes_content() -> None:
    assert hash_chunk("a") != hash_chunk("b")


def test_hash_chunk_encrypted_marker() -> None:
    assert hash_chunk("hello", encrypted=True).startswith("h:+")
    assert not hash_chunk("hello", encrypted=False).startswith("h:+")
