"""Query interface for recent vault changes via CouchDB _changes feed.

Provides sequence-based and timestamp-based filtering of vault modifications,
with persistent watermarking for resumable polling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class ChangeRecord:
    """A single change from the _changes feed."""

    path: str
    deleted: bool
    seq: int
    timestamp_ms: int  # unix ms
    note_size_bytes: int | None = None  # None if deleted or not available


class RecentChangesQuery:
    """Filter and query CouchDB _changes for vault modifications.

    Supports both sequence-based (reliable for incremental sync) and
    timestamp-based (human-friendly) filtering.
    """

    def __init__(self, since_seq: int = 0) -> None:
        """Initialize with optional watermark.

        Args:
            since_seq: Start from this sequence number on next poll.
                Typically loaded from persistent storage on startup.
        """
        self.since_seq = since_seq

    def update_watermark(self, seq: int) -> None:
        """Update the watermark after processing a batch."""
        self.since_seq = seq

    def parse_since_param(self, since: str | int | None) -> int:
        """Convert a since parameter to a sequence number.

        Args:
            since: One of:
                - int: CouchDB sequence number (returned directly)
                - "1h", "30m", "7d": relative time (converted to timestamp, then to seq)
                - ISO 8601 timestamp: absolute time
                - None: use self.since_seq (watermark)

        Returns:
            Sequence number or 0 if conversion fails.
        """
        if since is None:
            return self.since_seq

        if isinstance(since, int):
            return since

        if isinstance(since, str):
            # Try relative format: "1h", "30m", "7d", "5s"
            match = re.match(r"^(\d+)([smhd])$", since.lower().strip())
            if match:
                amount = int(match.group(1))
                unit = match.group(2)
                delta = {
                    "s": timedelta(seconds=amount),
                    "m": timedelta(minutes=amount),
                    "h": timedelta(hours=amount),
                    "d": timedelta(days=amount),
                }[unit]
                cutoff_time = datetime.now(UTC) - delta
                return int(cutoff_time.timestamp() * 1000)

            # Try ISO 8601: "2026-05-19T00:00:00Z" or "2026-05-19"
            try:
                if "T" in since:
                    dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(f"{since}T00:00:00+00:00")
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass

        return 0

    def filter_changes(
        self,
        changes: list[dict[str, Any]],
        path_prefix: str | None = None,
        include_deleted: bool = True,
        change_types: list[str] | None = None,
    ) -> list[ChangeRecord]:
        """Filter and convert raw _changes rows to ChangeRecords.

        Args:
            changes: Raw _changes rows from CouchDB.
            path_prefix: Filter to paths starting with this prefix (None = all).
            include_deleted: If False, omit deleted notes.
            change_types: Filter by type: ["create", "update", "delete"] (None = all).

        Returns:
            Filtered and parsed ChangeRecords.
        """
        result = []

        for row in changes:
            seq = row.get("seq")
            if seq is None:
                continue

            doc = row.get("doc", {})
            deleted = bool(doc.get("_deleted") or doc.get("deleted"))

            # If include_deleted=False, skip deleted notes
            if deleted and not include_deleted:
                continue

            # Determine change type
            if not deleted:
                change_type = "update" if "delete" not in row else "create"
            else:
                change_type = "delete"

            # If filtering by type, check it
            if change_types and change_type not in change_types:
                continue

            path = doc.get("path", "")
            if not path:
                continue

            # If path prefix filter, check it
            if path_prefix and not path.startswith(path_prefix):
                continue

            # Extract metadata
            mtime = doc.get("mtime", 0)
            size = doc.get("size", 0)

            record = ChangeRecord(
                path=path,
                deleted=deleted,
                seq=seq,
                timestamp_ms=mtime,
                note_size_bytes=size if not deleted else None,
            )
            result.append(record)

        return result
