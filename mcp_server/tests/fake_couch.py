"""In-memory fake of CouchDBClient for tests.

Implements the subset of the real client's surface the NoteRepository
uses, with realistic MVCC semantics:
  - `put` checks the `_rev` you supply against the stored one; mismatch
    raises CouchDBError(409, ...).
  - Successful writes bump `_rev` to `<n+1>-<hash>`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from obsidian_livesync_mcp.errors import CouchDBError


class FakeCouchDBClient:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:  # pragma: no cover
        pass

    def _next_rev(self, current: str | None, body: dict[str, Any]) -> str:
        seq = 1 if current is None else int(current.split("-", 1)[0]) + 1
        h = hashlib.md5(json.dumps(body, sort_keys=True).encode()).hexdigest()[:8]
        return f"{seq}-{h}"

    async def get(self, doc_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(doc_id)
        return None if doc is None else dict(doc)

    async def put(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc_id = doc["_id"]
        existing = self.docs.get(doc_id)
        provided_rev = doc.get("_rev")
        existing_rev = existing.get("_rev") if existing else None
        if existing_rev != provided_rev:
            raise CouchDBError(409, "conflict")
        body = {k: v for k, v in doc.items() if k != "_rev"}
        new_rev = self._next_rev(provided_rev, body)
        stored = dict(doc)
        stored["_rev"] = new_rev
        self.docs[doc_id] = stored
        return {"ok": True, "id": doc_id, "rev": new_rev}

    async def bulk_get(self, doc_ids: list[str]) -> list[dict[str, Any] | None]:
        return [await self.get(d) for d in doc_ids]

    async def bulk_docs(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for doc in docs:
            try:
                result = await self.put(doc)
                out.append(result)
            except CouchDBError as e:
                if e.status_code == 409:
                    out.append({"id": doc["_id"], "error": "conflict", "reason": "conflict"})
                else:  # pragma: no cover
                    raise
        return out

    async def all_docs(
        self,
        start_key: str | None = None,
        end_key: str | None = None,
        include_docs: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        keys = sorted(self.docs.keys())
        rows: list[dict[str, Any]] = []
        for k in keys:
            if start_key is not None and k < start_key:
                continue
            if end_key is not None and k > end_key:
                continue
            row: dict[str, Any] = {"id": k, "key": k}
            if include_docs:
                row["doc"] = dict(self.docs[k])
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
        return rows

    async def changes(
        self,
        since: str = "now",
        feed: str = "continuous",
        include_docs: bool = True,
        heartbeat_ms: int = 30_000,
    ) -> AsyncIterator[dict[str, Any]]:  # pragma: no cover
        if False:
            yield {}
