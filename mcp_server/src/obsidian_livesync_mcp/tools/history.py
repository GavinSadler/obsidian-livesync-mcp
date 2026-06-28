"""Recent-changes tool for querying vault modifications."""

from __future__ import annotations

from datetime import UTC, datetime

from ..couchdb import CouchDBClient
from ..livesync.recent_changes import RecentChangesQuery
from .models import ChangeItem, RecentChangesOutput


def _ms_to_iso(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class HistoryTools:
    """Adapters for querying CouchDB's _changes feed.

    Returns a snapshot of recent vault modifications. Two filter modes:
      - `since_seq`: CouchDB sequence number (reliable, resumable polling).
      - `since`: timestamp ("1h", "7d", or ISO 8601) — convenient but uses
        mtime, so clock skew across devices can result in missed events.

    For reliable incremental polling, store `watermark_seq` from each
    response and pass it as `since_seq` on the next call.
    """

    def __init__(self, couch: CouchDBClient) -> None:
        self.couch = couch
        self.query = RecentChangesQuery(since_seq=0)

    async def recent_changes(
        self,
        since_seq: int | None = None,
        since: str | None = None,
        path_prefix: str | None = None,
        include_deleted: bool = True,
        change_types: list[str] | None = None,
        limit: int = 100,
    ) -> RecentChangesOutput:
        """Return recent vault changes matching the filter.

        Args:
            since_seq: CouchDB sequence number to start from. Preferred for
                reliable incremental polling. If both since_seq and since
                are given, since_seq wins.
            since: Timestamp filter. "1h", "30m", "7d" (relative), or ISO 8601.
                Falls back to mtime comparison; clock-skew sensitive.
            path_prefix: Restrict to notes under this prefix.
            include_deleted: If False, omit deleted notes (default True).
            change_types: Filter by type: ["create", "update", "delete"].
                None = all types.
            limit: Max events to return (default 100, max 1000).

        Returns:
            RecentChangesOutput with changes, watermark, and truncation status.
        """
        limit = min(limit, 1000)

        # Resolve the starting point. Sequence-based wins if both given.
        if since_seq is not None:
            seq_param: int | str = since_seq
        elif since is not None:
            # Get all changes since this timestamp; we'll filter by mtime below.
            seq_param = 0
        else:
            seq_param = 0

        timestamp_ms_cutoff: int | None = None
        if since_seq is None and since is not None:
            timestamp_ms_cutoff = self.query.parse_since_param(since)

        # Drain the changes feed in non-streaming mode for a one-shot batch.
        # Using feed=normal so the iterator terminates instead of staying open.
        raw_changes: list[dict[str, object]] = []
        async for row in self.couch.changes(since=str(seq_param), feed="normal"):
            raw_changes.append(row)

        records = self.query.filter_changes(
            raw_changes,
            path_prefix=path_prefix,
            include_deleted=include_deleted,
            change_types=change_types,
        )

        # Apply timestamp cutoff if given
        if timestamp_ms_cutoff is not None:
            records = [r for r in records if r.timestamp_ms >= timestamp_ms_cutoff]

        # Sort by seq descending (most recent first)
        records.sort(key=lambda r: r.seq, reverse=True)

        total_available = len(records)
        truncated = total_available > limit
        records = records[:limit]

        # Build response
        items: list[ChangeItem] = []
        watermark_seq = 0
        for r in records:
            change_type = "delete" if r.deleted else "update"
            items.append(
                ChangeItem(
                    path=r.path,
                    change_type=change_type,
                    mtime=_ms_to_iso(r.timestamp_ms),
                    deleted=r.deleted,
                    seq=r.seq,
                    size_bytes=r.note_size_bytes,
                )
            )
            if r.seq > watermark_seq:
                watermark_seq = r.seq

        return RecentChangesOutput(
            changes=items,
            watermark_seq=watermark_seq,
            total_available=total_available,
            truncated=truncated,
        )
