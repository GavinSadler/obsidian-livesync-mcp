"""Tests for the NoteRepository CRUD layer against an in-memory CouchDB."""

from __future__ import annotations

from typing import cast

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.errors import (
    InvalidPathError,
    NoteAlreadyExistsError,
    NoteNotFoundError,
)
from obsidian_livesync_mcp.livesync.notes import NoteRepository

from .fake_couch import FakeCouchDBClient


@pytest.fixture
def repo() -> NoteRepository:
    fake = FakeCouchDBClient()
    return NoteRepository(cast(CouchDBClient, fake))


async def test_create_and_read_note(repo: NoteRepository) -> None:
    note = await repo.create("notes/hello.md", "# Hello\n\nWorld.")
    assert note.path == "notes/hello.md"
    assert note.content == "# Hello\n\nWorld."

    read = await repo.read("notes/hello.md")
    assert read is not None
    assert read.content == "# Hello\n\nWorld."
    assert read.ctime == note.ctime
    assert read.mtime == note.mtime


async def test_create_duplicate_raises(repo: NoteRepository) -> None:
    await repo.create("dup.md", "x")
    with pytest.raises(NoteAlreadyExistsError):
        await repo.create("dup.md", "y")


async def test_create_invalid_path_raises(repo: NoteRepository) -> None:
    with pytest.raises(InvalidPathError):
        await repo.create("notes/foo.txt", "x")  # not .md
    with pytest.raises(InvalidPathError):
        await repo.create("/abs/path.md", "x")
    with pytest.raises(InvalidPathError):
        await repo.create("../escape.md", "x")


async def test_read_missing_returns_none(repo: NoteRepository) -> None:
    assert await repo.read("missing.md") is None


async def test_update_preserves_ctime_bumps_mtime(repo: NoteRepository) -> None:
    created = await repo.create("upd.md", "v1")
    # Wait a millisecond so mtime can differ
    import time

    time.sleep(0.002)
    updated = await repo.update("upd.md", "v2")
    assert updated.ctime == created.ctime
    assert updated.mtime > created.mtime

    fresh = await repo.read("upd.md")
    assert fresh is not None
    assert fresh.content == "v2"


async def test_update_missing_raises(repo: NoteRepository) -> None:
    with pytest.raises(NoteNotFoundError):
        await repo.update("missing.md", "x")


async def test_delete_makes_note_unreadable(repo: NoteRepository) -> None:
    await repo.create("del.md", "bye")
    assert await repo.delete("del.md") is True
    assert await repo.read("del.md") is None
    # Second delete fails because note is already soft-deleted.
    with pytest.raises(NoteNotFoundError):
        await repo.delete("del.md")


async def test_create_resurrects_soft_deleted_note(repo: NoteRepository) -> None:
    created = await repo.create("res.md", "v1")
    await repo.delete("res.md")
    # ctime should be fresh (not preserved from before deletion); plugin
    # behavior — matches Obsidian's filesystem stat.
    resurrected = await repo.create("res.md", "v2")
    assert resurrected.content == "v2"
    assert resurrected.ctime >= created.ctime


async def test_move_renames_note(repo: NoteRepository) -> None:
    await repo.create("old.md", "content")
    moved = await repo.move("old.md", "new.md")
    assert moved.path == "new.md"
    assert moved.content == "content"
    assert await repo.read("old.md") is None
    assert (await repo.read("new.md")) is not None


async def test_move_same_path_raises(repo: NoteRepository) -> None:
    await repo.create("x.md", "y")
    with pytest.raises(InvalidPathError):
        await repo.move("x.md", "x.md")


async def test_move_missing_source_raises(repo: NoteRepository) -> None:
    with pytest.raises(NoteNotFoundError):
        await repo.move("missing.md", "new.md")


async def test_move_into_existing_raises(repo: NoteRepository) -> None:
    await repo.create("a.md", "1")
    await repo.create("b.md", "2")
    with pytest.raises(NoteAlreadyExistsError):
        await repo.move("a.md", "b.md")


async def test_list_paths_filters_chunks_and_deleted(repo: NoteRepository) -> None:
    await repo.create("a.md", "x")
    await repo.create("b.md", "y")
    await repo.create("notes/c.md", "z")
    await repo.delete("a.md")

    all_paths = [item["path"] for item in await repo.list_paths()]
    assert "a.md" not in all_paths  # soft-deleted
    assert set(all_paths) == {"b.md", "notes/c.md"}


async def test_list_paths_prefix_filter(repo: NoteRepository) -> None:
    await repo.create("projects/p1.md", "x")
    await repo.create("projects/p2.md", "y")
    await repo.create("daily/d1.md", "z")

    projects = [item["path"] for item in await repo.list_paths(path_prefix="projects/")]
    assert set(projects) == {"projects/p1.md", "projects/p2.md"}


async def test_round_trip_large_content(repo: NoteRepository) -> None:
    # Multi-chunk content to exercise the splitter + assembler.
    body = "Line A.\nLine B.\nLine C.\n" * 200
    await repo.create("big.md", body)
    read = await repo.read("big.md")
    assert read is not None
    assert read.content == body
