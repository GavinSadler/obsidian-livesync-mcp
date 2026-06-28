"""Unit tests for the async CouchDB HTTP client.

These drive the real `CouchDBClient` over an `httpx.MockTransport`, so the
request building (URLs, methods, bodies) and response parsing are exercised
without a live server. Live, end-to-end behaviour against an actual CouchDB
is covered separately by `test_integration_live.py` (opt-in).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.errors import CouchDBError

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> CouchDBClient:
    """A CouchDBClient whose transport is a MockTransport running `handler`."""
    return CouchDBClient(
        "http://couch.example:5984",
        "vault",
        username="admin",
        password="pw",
        transport=httpx.MockTransport(handler),
    )


def _record(requests: list[httpx.Request]) -> Handler:
    """Handler that records requests and returns a canned empty-ok response."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    return handler


# --------------------------------------------------------------------------
# URL construction — the _local / _design slash must stay literal
# --------------------------------------------------------------------------


# Note: assert on `url.raw_path` (the wire form), not `url.path` — the latter
# percent-decodes, so it can't tell an encoded `%2F` from a literal `/`.


async def test_get_encodes_slash_in_regular_doc_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"_id": "folder/note.md"})

    async with _client(handler) as c:
        await c.get("folder/note.md")
    # The slash in a normal id is percent-encoded into a single path segment.
    assert seen[0].url.raw_path == b"/vault/folder%2Fnote.md"


async def test_get_keeps_local_namespace_slash_literal() -> None:
    """The sync-parameters local doc must hit `/vault/_local/<id>`, not `_local%2F`."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        await c.get("_local/obsidian_livesync_sync_parameters")
    assert seen[0].url.raw_path == b"/vault/_local/obsidian_livesync_sync_parameters"


async def test_get_keeps_design_namespace_slash_literal() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as c:
        await c.get("_design/by_path")
    assert seen[0].url.raw_path == b"/vault/_design/by_path"


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------


async def test_get_returns_doc_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"_id": "readme.md", "type": "plain"})

    async with _client(handler) as c:
        doc = await c.get("readme.md")
    assert doc == {"_id": "readme.md", "type": "plain"}


async def test_get_returns_none_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    async with _client(handler) as c:
        assert await c.get("missing.md") is None


async def test_get_raises_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as c:
        with pytest.raises(CouchDBError) as exc:
            await c.get("readme.md")
    assert exc.value.status_code == 500


# --------------------------------------------------------------------------
# put
# --------------------------------------------------------------------------


async def test_put_sends_json_body_to_doc_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"ok": True, "id": "n.md", "rev": "1-abc"})

    async with _client(handler) as c:
        resp = await c.put({"_id": "n.md", "type": "plain"})
    assert resp["ok"] is True
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/vault/n.md"
    assert json.loads(seen[0].content) == {"_id": "n.md", "type": "plain"}


async def test_put_raises_on_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "conflict"})

    async with _client(handler) as c:
        with pytest.raises(CouchDBError) as exc:
            await c.put({"_id": "n.md"})
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------
# bulk_get / bulk_docs
# --------------------------------------------------------------------------


async def test_bulk_get_unwraps_ok_entries_and_nulls_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/vault/_bulk_get"
        body = json.loads(request.content)
        assert body == {"docs": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        return httpx.Response(
            200,
            json={
                "results": [
                    {"docs": [{"ok": {"_id": "a", "data": "x"}}]},
                    {"docs": [{"error": {"id": "b", "error": "not_found"}}]},
                    {"docs": []},
                ]
            },
        )

    async with _client(handler) as c:
        out = await c.bulk_get(["a", "b", "c"])
    assert out == [{"_id": "a", "data": "x"}, None, None]


async def test_bulk_get_empty_is_no_request() -> None:
    requests: list[httpx.Request] = []
    async with _client(_record(requests)) as c:
        assert await c.bulk_get([]) == []
    assert requests == []


async def test_bulk_docs_posts_docs_and_returns_results() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=[{"ok": True, "id": "a", "rev": "1-x"}])

    async with _client(handler) as c:
        out = await c.bulk_docs([{"_id": "a"}])
    assert out == [{"ok": True, "id": "a", "rev": "1-x"}]
    assert seen[0].url.path == "/vault/_bulk_docs"
    assert json.loads(seen[0].content) == {"docs": [{"_id": "a"}]}


async def test_bulk_docs_empty_is_no_request() -> None:
    requests: list[httpx.Request] = []
    async with _client(_record(requests)) as c:
        assert await c.bulk_docs([]) == []
    assert requests == []


# --------------------------------------------------------------------------
# all_docs
# --------------------------------------------------------------------------


async def test_all_docs_passes_range_params_and_returns_rows() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"rows": [{"id": "a"}, {"id": "b"}]})

    async with _client(handler) as c:
        rows = await c.all_docs(start_key="projects/", end_key="projects/￰", include_docs=True)
    assert rows == [{"id": "a"}, {"id": "b"}]
    q = seen[0].url.params
    # startkey/endkey are JSON-encoded (quoted) per CouchDB convention.
    assert q["startkey"] == '"projects/"'
    assert q["include_docs"] == "true"


# --------------------------------------------------------------------------
# changes (continuous feed, line-delimited JSON)
# --------------------------------------------------------------------------


async def test_changes_yields_rows_and_skips_blank_lines() -> None:
    body = (
        json.dumps({"seq": 1, "id": "a"})
        + "\n\n"  # heartbeat blank line
        + json.dumps({"seq": 2, "id": "b"})
        + "\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/vault/_changes"
        return httpx.Response(200, text=body)

    rows = []
    async with _client(handler) as c:
        async for row in c.changes(since="0", feed="continuous"):
            rows.append(row)
    assert rows == [{"seq": 1, "id": "a"}, {"seq": 2, "id": "b"}]


async def test_changes_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async with _client(handler) as c:
        with pytest.raises(CouchDBError) as exc:
            async for _ in c.changes(since="0"):
                pass
    assert exc.value.status_code == 401
