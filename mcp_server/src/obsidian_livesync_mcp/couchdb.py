"""Thin async CouchDB HTTP client.

We don't use a third-party CouchDB library because we only need a small slice
of the API (get/put/bulk_docs/all_docs/changes) and want full control over
how documents are serialized.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from .errors import CouchDBError


class CouchDBClient:
    """Minimal async CouchDB client over httpx."""

    def __init__(
        self,
        url: str,
        database: str,
        username: str | None = None,
        password: str | None = None,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        auth = (username, password) if username and password else None
        self._base = url.rstrip("/")
        self._db = database
        # `transport` is a test seam (e.g. httpx.MockTransport); None uses the
        # default network transport.
        self._client = httpx.AsyncClient(
            auth=auth,
            verify=verify_tls,
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CouchDBClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _doc_url(self, doc_id: str) -> str:
        # CouchDB allows `/` in doc IDs, but it must be percent-encoded so the
        # whole id is one path segment — except for the `_design/` and `_local/`
        # namespaces, whose leading `/` is structural and must stay literal
        # (e.g. the sync-parameters doc `_local/obsidian_livesync_sync_parameters`).
        for prefix in ("_design/", "_local/"):
            if doc_id.startswith(prefix):
                return f"{self._base}/{self._db}/{prefix}{quote(doc_id[len(prefix) :], safe='')}"
        return f"{self._base}/{self._db}/{quote(doc_id, safe='')}"

    def _db_url(self, path: str = "") -> str:
        return f"{self._base}/{self._db}/{path.lstrip('/')}" if path else f"{self._base}/{self._db}"

    async def get(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document by ID. Returns None on 404."""
        resp = await self._client.get(self._doc_url(doc_id))
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise CouchDBError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def put(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Create or update a single document. Returns CouchDB response.

        On 409 (conflict), raises CouchDBError(409, ...) — caller decides
        whether to refetch + retry.
        """
        doc_id = doc["_id"]
        resp = await self._client.put(self._doc_url(doc_id), json=doc)
        if resp.status_code not in (200, 201, 202):
            raise CouchDBError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def bulk_get(self, doc_ids: list[str]) -> list[dict[str, Any] | None]:
        """Fetch many documents in one round trip via _bulk_get.

        Returns one entry per requested ID, in order. None for any ID
        that wasn't found.
        """
        if not doc_ids:
            return []
        body = {"docs": [{"id": doc_id} for doc_id in doc_ids]}
        resp = await self._client.post(self._db_url("_bulk_get"), json=body)
        if resp.status_code != 200:
            raise CouchDBError(resp.status_code, resp.text)
        data = resp.json()
        out: list[dict[str, Any] | None] = []
        for result in data.get("results", []):
            docs = result.get("docs", [])
            if not docs:
                out.append(None)
                continue
            entry = docs[0]
            if "ok" in entry:
                out.append(entry["ok"])
            else:
                out.append(None)
        return out

    async def bulk_docs(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create/update many documents in one round trip via _bulk_docs.

        Each result entry has either `ok: true` with `id`/`rev`, or
        `error`/`reason` for failures (e.g., conflicts).
        """
        if not docs:
            return []
        body = {"docs": docs}
        resp = await self._client.post(self._db_url("_bulk_docs"), json=body)
        if resp.status_code not in (200, 201):
            raise CouchDBError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def all_docs(
        self,
        start_key: str | None = None,
        end_key: str | None = None,
        include_docs: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Range query over _all_docs. Returns the `rows` array directly."""
        params: dict[str, str] = {}
        if start_key is not None:
            params["startkey"] = json.dumps(start_key)
        if end_key is not None:
            params["endkey"] = json.dumps(end_key)
        if include_docs:
            params["include_docs"] = "true"
        if limit is not None:
            params["limit"] = str(limit)
        resp = await self._client.get(self._db_url("_all_docs"), params=params)
        if resp.status_code != 200:
            raise CouchDBError(resp.status_code, resp.text)
        return resp.json().get("rows", [])  # type: ignore[no-any-return]

    async def changes(
        self,
        since: str = "now",
        feed: str = "continuous",
        include_docs: bool = True,
        heartbeat_ms: int = 30_000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the _changes feed. Yields one change row at a time.

        With feed='continuous' the connection stays open and each row is
        a separate JSON line; with feed='normal' the whole response is
        one JSON object — we handle both.
        """
        params: dict[str, str] = {
            "since": since,
            "feed": feed,
            "include_docs": "true" if include_docs else "false",
        }
        if feed == "continuous":
            params["heartbeat"] = str(heartbeat_ms)

        async with self._client.stream(
            "GET", self._db_url("_changes"), params=params, timeout=None
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                raise CouchDBError(resp.status_code, body)
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
