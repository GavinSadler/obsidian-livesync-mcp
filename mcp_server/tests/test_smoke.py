"""Smoke tests that just import the package and check basic invariants."""

from __future__ import annotations


def test_package_imports() -> None:
    import obsidian_livesync_mcp

    assert obsidian_livesync_mcp.__version__


def test_subpackages_import() -> None:
    from obsidian_livesync_mcp import couchdb, server  # noqa: F401
    from obsidian_livesync_mcp.livesync import (  # noqa: F401
        chunks,
        encryption,
        models,
        notes,
        paths,
    )
    from obsidian_livesync_mcp.tools import notes as tool_notes  # noqa: F401
    from obsidian_livesync_mcp.tools import search as tool_search  # noqa: F401
    from obsidian_livesync_mcp.vector import embeddings, indexer, store  # noqa: F401
