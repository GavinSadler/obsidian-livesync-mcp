"""Tests for content chunking and assembly."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.chunks import assemble_chunks


def test_assemble_chunks_empty() -> None:
    assert assemble_chunks([]) == ""


def test_assemble_chunks_concatenates_in_order() -> None:
    assert assemble_chunks(["hello, ", "world", "!"]) == "hello, world!"


@pytest.mark.skip(reason="split_content not implemented yet")
def test_split_then_assemble_is_identity(sample_note_content: str) -> None:
    from obsidian_livesync_mcp.livesync.chunks import split_content

    chunks = split_content(sample_note_content)
    assert assemble_chunks(chunks) == sample_note_content


@pytest.mark.skip(reason="hash_chunk not implemented yet")
def test_hash_chunk_is_stable() -> None:
    from obsidian_livesync_mcp.livesync.chunks import hash_chunk

    assert hash_chunk("hello") == hash_chunk("hello")
    assert hash_chunk("hello").startswith("h:")
