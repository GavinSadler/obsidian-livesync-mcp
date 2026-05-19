"""Tests for recent changes filtering and sequence-based queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from obsidian_livesync_mcp.livesync.recent_changes import (
    ChangeRecord,
    RecentChangesQuery,
)


def test_change_record_creation() -> None:
    record = ChangeRecord(
        path="notes/example.md",
        deleted=False,
        seq=42,
        timestamp_ms=1716000000000,
        note_size_bytes=512,
    )
    assert record.path == "notes/example.md"
    assert record.deleted is False
    assert record.seq == 42
    assert record.note_size_bytes == 512


def test_recent_changes_query_init() -> None:
    query = RecentChangesQuery(since_seq=100)
    assert query.since_seq == 100


def test_recent_changes_query_parse_since_none() -> None:
    query = RecentChangesQuery(since_seq=42)
    result = query.parse_since_param(None)
    assert result == 42


def test_recent_changes_query_parse_since_int() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param(123)
    assert result == 123


def test_recent_changes_query_parse_since_relative_hours() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param("1h")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    one_hour_ago = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
    # Result should be close to one hour ago
    assert abs(result - one_hour_ago) < 60000  # Within 1 minute


def test_recent_changes_query_parse_since_relative_minutes() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param("30m")
    thirty_min_ago = int(
        (datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp() * 1000
    )
    assert abs(result - thirty_min_ago) < 60000


def test_recent_changes_query_parse_since_relative_days() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param("7d")
    seven_days_ago = int(
        (datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000
    )
    assert abs(result - seven_days_ago) < 60000


def test_recent_changes_query_parse_since_iso_timestamp() -> None:
    query = RecentChangesQuery()
    iso = "2026-05-19T00:00:00Z"
    result = query.parse_since_param(iso)
    assert isinstance(result, int)
    assert result > 0


def test_recent_changes_query_parse_since_iso_date() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param("2026-05-19")
    assert isinstance(result, int)
    assert result > 0


def test_recent_changes_query_parse_since_invalid() -> None:
    query = RecentChangesQuery()
    result = query.parse_since_param("invalid")
    assert result == 0


def test_recent_changes_query_update_watermark() -> None:
    query = RecentChangesQuery(since_seq=0)
    assert query.since_seq == 0
    query.update_watermark(999)
    assert query.since_seq == 999


def test_recent_changes_query_filter_basic() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "a.md", "mtime": 1000, "size": 100}},
        {"seq": 2, "doc": {"path": "b.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes)
    assert len(result) == 2
    assert result[0].path == "a.md"
    assert result[1].path == "b.md"


def test_recent_changes_query_filter_path_prefix() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "projects/foo.md", "mtime": 1000, "size": 100}},
        {"seq": 2, "doc": {"path": "archive/bar.md", "mtime": 2000, "size": 200}},
        {"seq": 3, "doc": {"path": "projects/baz.md", "mtime": 3000, "size": 300}},
    ]
    result = query.filter_changes(changes, path_prefix="projects/")
    assert len(result) == 2
    assert all(r.path.startswith("projects/") for r in result)


def test_recent_changes_query_filter_deleted_included() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "deleted.md", "mtime": 1000, "size": 100, "deleted": True}},
        {"seq": 2, "doc": {"path": "active.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes, include_deleted=True)
    assert len(result) == 2
    assert result[0].deleted is True
    assert result[1].deleted is False


def test_recent_changes_query_filter_deleted_excluded() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "deleted.md", "mtime": 1000, "size": 100, "deleted": True}},
        {"seq": 2, "doc": {"path": "active.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes, include_deleted=False)
    assert len(result) == 1
    assert result[0].path == "active.md"


def test_recent_changes_query_filter_change_types() -> None:
    query = RecentChangesQuery()
    changes = [
        {
            "seq": 1,
            "doc": {"path": "deleted.md", "mtime": 1000, "size": 100, "deleted": True},
        },
        {"seq": 2, "doc": {"path": "updated.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes, change_types=["delete"])
    assert len(result) == 1
    assert result[0].deleted is True


def test_recent_changes_query_filter_missing_seq() -> None:
    query = RecentChangesQuery()
    changes = [
        {"doc": {"path": "a.md"}},  # Missing seq
        {"seq": 2, "doc": {"path": "b.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes)
    assert len(result) == 1
    assert result[0].path == "b.md"


def test_recent_changes_query_filter_missing_path() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"mtime": 1000, "size": 100}},  # Missing path
        {"seq": 2, "doc": {"path": "b.md", "mtime": 2000, "size": 200}},
    ]
    result = query.filter_changes(changes)
    assert len(result) == 1
    assert result[0].path == "b.md"


def test_recent_changes_query_filter_deleted_flag() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "a.md", "mtime": 1000, "size": 100, "_deleted": True}},
        {"seq": 2, "doc": {"path": "b.md", "mtime": 2000, "size": 200, "deleted": True}},
        {"seq": 3, "doc": {"path": "c.md", "mtime": 3000, "size": 300}},
    ]
    result = query.filter_changes(changes)
    assert result[0].deleted is True  # _deleted flag
    assert result[1].deleted is True  # deleted flag
    assert result[2].deleted is False


def test_recent_changes_query_note_size_on_delete() -> None:
    query = RecentChangesQuery()
    changes = [
        {"seq": 1, "doc": {"path": "deleted.md", "mtime": 1000, "deleted": True}},
        {"seq": 2, "doc": {"path": "active.md", "mtime": 2000, "size": 500}},
    ]
    result = query.filter_changes(changes)
    assert result[0].note_size_bytes is None  # No size for deleted notes
    assert result[1].note_size_bytes == 500
