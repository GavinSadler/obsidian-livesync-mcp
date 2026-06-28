"""Unit tests for the live fixture loader's pure logic + request sequencing.

The HTTP calls are driven through an ``httpx.MockTransport`` so we can assert
the exact CouchDB request sequence (drop → create → bulk_docs → local PUT) and
that ``_rev`` is stripped, without needing a live server. Actually loading into
a real CouchDB is exercised by ``test_integration_live.py`` (opt-in).
"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts.load_fixture import (
    _read_docs,
    _strip_rev,
    load_fixture_into_couch,
)
from tests.fixture_loader import available_vaults


def test_strip_rev_removes_rev_and_revisions() -> None:
    doc = {"_id": "a", "_rev": "3-xyz", "_revisions": {"start": 3}, "type": "plain"}
    assert _strip_rev(doc) == {"_id": "a", "type": "plain"}


@pytest.mark.skipif("plain" not in available_vaults(), reason="plain fixture missing")
def test_read_docs_separates_local_doc() -> None:
    docs, sync_params = _read_docs("plain")
    # Regular docs include notes + chunks, and never a `_local/` doc.
    assert docs
    assert not any(str(d["_id"]).startswith("_local/") for d in docs)
    # The sync-parameters doc is surfaced separately.
    assert sync_params is not None
    assert sync_params["_id"] == "_local/obsidian_livesync_sync_parameters"


def test_read_docs_missing_vault_raises() -> None:
    with pytest.raises(FileNotFoundError):
        _read_docs("does-not-exist")


@pytest.mark.skipif("plain" not in available_vaults(), reason="plain fixture missing")
def test_load_fixture_issues_expected_request_sequence() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.raw_path.decode()
        if request.method == "DELETE":
            return httpx.Response(200, json={"ok": True})
        if request.method == "PUT" and path == "/mcp-live-plain":
            return httpx.Response(201, json={"ok": True})
        if path.endswith("/_bulk_docs"):
            body = json.loads(request.content)
            return httpx.Response(201, json=[{"ok": True, "id": d["_id"]} for d in body["docs"]])
        if request.method == "PUT":  # the _local sync-parameters doc
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404)

    summary = load_fixture_into_couch(
        base_url="http://couch.example:5984",
        database="mcp-live-plain",
        vault="plain",
        username="admin",
        password="pw",
        reset=True,
        transport=httpx.MockTransport(handler),
    )

    methods_paths = [(r.method, r.url.raw_path.decode()) for r in requests]
    # 1) drop, 2) create, 3+) bulk_docs, last) local PUT.
    assert methods_paths[0] == ("DELETE", "/mcp-live-plain")
    assert methods_paths[1] == ("PUT", "/mcp-live-plain")
    assert any(m == "POST" and p == "/mcp-live-plain/_bulk_docs" for m, p in methods_paths)

    # The local doc PUT keeps the `_local/` slash literal (not percent-encoded).
    assert (
        "PUT",
        "/mcp-live-plain/_local/obsidian_livesync_sync_parameters",
    ) in methods_paths

    # `_rev` is stripped from every bulk-inserted doc.
    for r in requests:
        if r.url.raw_path.decode().endswith("/_bulk_docs"):
            for d in json.loads(r.content)["docs"]:
                assert "_rev" not in d

    assert summary["docs"] > 0
    assert summary["sync_parameters"] == 1
