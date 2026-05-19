"""Tests for path ↔ doc-ID encoding."""

from __future__ import annotations

import pytest

from obsidian_livesync_mcp.livesync.paths import (
    id_to_path,
    is_chunk_id,
    is_obfuscated_id,
    path_to_id,
)


def test_is_chunk_id_recognizes_normal_chunk() -> None:
    assert is_chunk_id("h:abcdef123") is True


def test_is_chunk_id_recognizes_encrypted_chunk() -> None:
    assert is_chunk_id("h:+abcdef123") is True


def test_is_chunk_id_rejects_path() -> None:
    assert is_chunk_id("notes/foo.md") is False


def test_is_obfuscated_id() -> None:
    assert is_obfuscated_id("f:deadbeef") is True
    assert is_obfuscated_id("notes/foo.md") is False


def test_path_to_id_plain_roundtrip() -> None:
    path = "notes/folder/file.md"
    assert id_to_path(path_to_id(path)) == path


def test_path_to_id_leading_underscore_is_escaped() -> None:
    # Per LiveSync, paths starting with `_` get a leading slash added to
    # avoid colliding with CouchDB reserved IDs (_design/, _local/).
    assert path_to_id("_template.md") == "/_template.md"
    assert id_to_path("/_template.md") == "_template.md"


def test_path_to_id_empty_raises() -> None:
    with pytest.raises(ValueError):
        path_to_id("")


def test_obfuscated_path_to_id_requires_passphrase() -> None:
    with pytest.raises(ValueError):
        path_to_id("foo.md", obfuscate=True)


def test_obfuscated_path_to_id_is_deterministic() -> None:
    a = path_to_id("foo.md", obfuscate=True, passphrase="hunter2")
    b = path_to_id("foo.md", obfuscate=True, passphrase="hunter2")
    assert a == b
    assert a.startswith("f:")
    assert len(a) == len("f:") + 64  # SHA-256 hex


def test_obfuscated_path_to_id_differs_by_passphrase() -> None:
    a = path_to_id("foo.md", obfuscate=True, passphrase="alpha")
    b = path_to_id("foo.md", obfuscate=True, passphrase="beta")
    assert a != b


def test_obfuscated_id_to_path_needs_fallback() -> None:
    obfuscated = path_to_id("foo.md", obfuscate=True, passphrase="x")
    with pytest.raises(ValueError):
        id_to_path(obfuscated)
    assert id_to_path(obfuscated, fallback_path="foo.md") == "foo.md"
