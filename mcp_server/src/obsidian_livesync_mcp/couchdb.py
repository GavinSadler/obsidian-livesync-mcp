"""Thin async CouchDB HTTP client.

We don't use a third-party CouchDB library because we only need a small slice
of the API (get/put/bulk_docs/all_docs/changes) and want full control over
how documents are serialized.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class CouchDBClient:
    """Minimal async CouchDB client over httpx."""

    def __init__(
        self,
        url: str,
        database: str,
        username: str | None = None,
        password: str | None = None,
        verify_tls: bool = True,
    ) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def get(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document by ID. Returns None on 404."""
        raise NotImplementedError

    async def put(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Create or update a single document. Returns CouchDB response."""
        raise NotImplementedError

    async def bulk_get(self, doc_ids: list[str]) -> list[dict[str, Any] | None]:
        """Fetch many documents in one round trip via _bulk_get."""
        raise NotImplementedError

    async def bulk_docs(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create/update many documents in one round trip via _bulk_docs."""
        raise NotImplementedError

    async def all_docs(
        self,
        start_key: str | None = None,
        end_key: str | None = None,
        include_docs: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Range query over _all_docs."""
        raise NotImplementedError

    async def changes(
        self,
        since: str = "now",
        feed: str = "continuous",
        include_docs: bool = True,
        heartbeat_ms: int = 30_000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the _changes feed. Yields one change row at a time."""
        raise NotImplementedError
        yield  # pragma: no cover  -- for type checker
