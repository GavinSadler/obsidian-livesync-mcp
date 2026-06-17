"""Load an exported-vault fixture into a real CouchDB database.

This is the live counterpart to ``tests/fixture_loader.py`` (which seeds an
in-memory ``FakeCouchDBClient``): it pushes the same genuine plugin documents
to an actual CouchDB over HTTP, so the opt-in live integration tests — and
manual experimentation — can run the real client/repository against real data.

The fixture format is a ``_all_docs?include_docs=true`` dump
(``all_docs.json`` → ``rows[].doc``) plus the ``_local`` sync-parameters doc
(``sync_parameters.json``). Loading therefore means:

  1. (optionally) drop + recreate the database,
  2. strip ``_rev`` from every doc and ``POST`` them in batches to ``_bulk_docs``
     (``_id`` is preserved — all that matters for content-addressed reads),
  3. ``PUT`` the ``_local/obsidian_livesync_sync_parameters`` doc so the PBKDF2
     salt is fetchable at startup.

CLI usage::

    uv run python -m scripts.load_fixture --vault plain \\
        --url http://127.0.0.1:5989 --db mcp-live-plain \\
        --user admin --password testpassword --reset

Credentials/URL fall back to ``COUCHDB_URL`` / ``COUCHDB_USERNAME`` /
``COUCHDB_PASSWORD`` when the flags are omitted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

_BULK_BATCH = 500


def _read_docs(vault: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Return (regular docs, sync-parameters local doc) for a fixture vault."""
    vault_dir = FIXTURES_DIR / vault
    all_docs_path = vault_dir / "all_docs.json"
    if not all_docs_path.exists():
        raise FileNotFoundError(f"fixture vault {vault!r} missing: {all_docs_path}")

    all_docs = json.loads(all_docs_path.read_text())
    docs: list[dict[str, Any]] = []
    for row in all_docs.get("rows", []):
        doc = row.get("doc")
        if not doc:
            continue
        # `_all_docs` never includes `_local` docs, but guard anyway.
        if str(doc.get("_id", "")).startswith("_local/"):
            continue
        docs.append(doc)

    sync_params: dict[str, Any] | None = None
    sync_params_path = vault_dir / "sync_parameters.json"
    if sync_params_path.exists():
        sp = json.loads(sync_params_path.read_text())
        if isinstance(sp, dict) and "_id" in sp:
            sync_params = sp

    return docs, sync_params


def _strip_rev(doc: dict[str, Any]) -> dict[str, Any]:
    """Copy without `_rev`/`_revisions` so a fresh insert (new_edits) accepts it."""
    return {k: v for k, v in doc.items() if k not in ("_rev", "_revisions")}


def load_fixture_into_couch(
    *,
    base_url: str,
    database: str,
    vault: str,
    username: str | None = None,
    password: str | None = None,
    verify_tls: bool = True,
    reset: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, int]:
    """Load fixture ``vault`` into ``database`` on the CouchDB at ``base_url``.

    Returns a small summary dict: ``{"docs": N, "sync_parameters": 0|1}``.
    Idempotent when ``reset=True`` (drops + recreates the DB first).

    ``transport`` is a test seam (e.g. ``httpx.MockTransport``); None uses the
    default network transport.
    """
    base = base_url.rstrip("/")
    auth = (username, password) if username and password else None
    docs, sync_params = _read_docs(vault)

    with httpx.Client(
        auth=auth,
        verify=verify_tls,
        timeout=httpx.Timeout(60.0, connect=10.0),
        transport=transport,
    ) as c:
        db_url = f"{base}/{database}"

        if reset:
            resp = c.delete(db_url)
            if resp.status_code not in (200, 202, 404):
                raise RuntimeError(
                    f"failed to drop db {database!r}: {resp.status_code} {resp.text}"
                )

        # Create the DB (412 = already exists, which is fine).
        resp = c.put(db_url)
        if resp.status_code not in (201, 202, 412):
            raise RuntimeError(f"failed to create db {database!r}: {resp.status_code} {resp.text}")

        # Bulk-insert the regular docs (notes + chunks) with fresh revisions.
        written = 0
        for start in range(0, len(docs), _BULK_BATCH):
            batch = [_strip_rev(d) for d in docs[start : start + _BULK_BATCH]]
            resp = c.post(f"{db_url}/_bulk_docs", json={"docs": batch})
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"_bulk_docs failed: {resp.status_code} {resp.text}")
            for result in resp.json():
                err = result.get("error")
                if err and err != "conflict":
                    raise RuntimeError(f"doc {result.get('id')!r} failed: {result.get('reason')}")
            written += len(batch)

        # PUT the local sync-parameters doc at its literal `_local/...` path so
        # the salt is fetchable. (Local docs are not part of `_bulk_docs`.)
        wrote_sync = 0
        if sync_params is not None:
            doc_id = str(sync_params["_id"])
            prefix, _, rest = doc_id.partition("/")
            url = f"{db_url}/{prefix}/{rest}" if prefix == "_local" else f"{db_url}/{doc_id}"
            resp = c.put(url, json=_strip_rev(sync_params))
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError(f"failed to write {doc_id!r}: {resp.status_code} {resp.text}")
            wrote_sync = 1

    return {"docs": written, "sync_parameters": wrote_sync}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load a vault fixture into a real CouchDB.")
    p.add_argument("--vault", required=True, help="Fixture name (plain / encrypted / obfuscated).")
    p.add_argument("--url", default=os.environ.get("COUCHDB_URL"), help="CouchDB base URL.")
    p.add_argument("--db", required=True, help="Target database name.")
    p.add_argument("--user", default=os.environ.get("COUCHDB_USERNAME"))
    p.add_argument("--password", default=os.environ.get("COUCHDB_PASSWORD"))
    p.add_argument("--no-verify-tls", action="store_true", help="Skip TLS verification.")
    p.add_argument("--reset", action="store_true", help="Drop + recreate the DB first.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.url:
        print("error: --url or COUCHDB_URL is required", file=sys.stderr)
        return 2
    summary = load_fixture_into_couch(
        base_url=args.url,
        database=args.db,
        vault=args.vault,
        username=args.user,
        password=args.password,
        verify_tls=not args.no_verify_tls,
        reset=args.reset,
    )
    print(
        f"loaded vault {args.vault!r} into {args.url}/{args.db}: "
        f"{summary['docs']} docs, {summary['sync_parameters']} sync-parameters doc"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
