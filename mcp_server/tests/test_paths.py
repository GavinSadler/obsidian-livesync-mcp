"""Tests for path ↔ doc-ID encoding."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.paths import is_chunk_id, is_obfuscated_id


def test_is_chunk_id_recognizes_normal_chunk() -> None:
    assert is_chunk_id("h:abcdef123") is True


def test_is_chunk_id_recognizes_encrypted_chunk() -> None:
    assert is_chunk_id("h:+abcdef123") is True


def test_is_chunk_id_rejects_path() -> None:
    assert is_chunk_id("notes/foo.md") is False


def test_is_obfuscated_id() -> None:
    assert is_obfuscated_id("f:deadbeef") is True
    assert is_obfuscated_id("notes/foo.md") is False


@pytest.mark.skip(reason="path_to_id not implemented yet")
def test_path_to_id_plain_roundtrip() -> None:
    from obsidian_livesync_mcp.livesync.paths import id_to_path, path_to_id

    path = "notes/folder/file.md"
    assert id_to_path(path_to_id(path)) == path


@pytest.mark.skip(reason="path_to_id not implemented yet")
def test_path_to_id_leading_underscore_is_escaped() -> None:
    from obsidian_livesync_mcp.livesync.paths import path_to_id

    # Per LiveSync, paths starting with `_` get a leading slash added to
    # avoid collisions with CouchDB design docs (`_design/...`).
    assert path_to_id("_template.md") == "/_template.md"
