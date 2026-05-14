"""MCP server entry point.

Wires together: configuration → CouchDB client → NoteRepository →
{NoteTools, SearchTools} → MCP server; plus a background Indexer task.
"""

from __future__ import annotations


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
