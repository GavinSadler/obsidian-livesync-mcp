"""Load real exported-vault fixtures into a FakeCouchDBClient.

These fixtures are JSON snapshots of an actual Obsidian LiveSync CouchDB
database (`_all_docs?include_docs=true` plus the `_local` sync-parameters
doc), produced by the real plugin. They let the integration tests run the
real `NoteRepository` against genuine plugin output without a live CouchDB.

Vault directories live under `mcp_server/fixtures/<name>/`:
  - all_docs.json         — the full document dump
  - sync_parameters.json  — `_local/obsidian_livesync_sync_parameters`
                            (carries the PBKDF2 salt + protocol version)
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fake_couch import FakeCouchDBClient

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass
class LoadedVault:
    """A fixture vault seeded into an in-memory CouchDB."""

    couch: FakeCouchDBClient
    salt: bytes | None
    protocol_version: int | None
    raw_docs: dict[str, dict[str, Any]]

    def note_ids(self) -> list[str]:
        """Document IDs that look like note docs (`.md`, not chunks/internal)."""
        return [
            doc_id
            for doc_id in self.raw_docs
            if doc_id.endswith(".md") and not doc_id.startswith(("h:", "_"))
        ]

    def chunk_ids(self) -> list[str]:
        return [doc_id for doc_id in self.raw_docs if doc_id.startswith("h:")]


def available_vaults() -> list[str]:
    """Names of fixture vaults present on disk (e.g. ['plain'])."""
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(
        p.name for p in FIXTURES_DIR.iterdir() if p.is_dir() and (p / "all_docs.json").exists()
    )


def load_vault(name: str) -> LoadedVault:
    """Seed a `FakeCouchDBClient` from the exported fixture named `name`.

    Raises FileNotFoundError if the vault export is missing, so a skipped
    test gives a clear reason rather than a cryptic KeyError.
    """
    vault_dir = FIXTURES_DIR / name
    all_docs_path = vault_dir / "all_docs.json"
    if not all_docs_path.exists():
        raise FileNotFoundError(f"fixture vault {name!r} missing: {all_docs_path}")

    all_docs = json.loads(all_docs_path.read_text())
    couch = FakeCouchDBClient()
    raw_docs: dict[str, dict[str, Any]] = {}
    for row in all_docs.get("rows", []):
        doc = row.get("doc")
        if not doc:
            continue
        couch.docs[doc["_id"]] = doc
        raw_docs[doc["_id"]] = doc

    salt: bytes | None = None
    protocol_version: int | None = None
    sync_params_path = vault_dir / "sync_parameters.json"
    if sync_params_path.exists():
        sp = json.loads(sync_params_path.read_text())
        # Seed it under its real _id so fetch_pbkdf2_salt() finds it too.
        if isinstance(sp, dict) and "_id" in sp:
            couch.docs[sp["_id"]] = sp
            raw_docs[sp["_id"]] = sp
        encoded = sp.get("pbkdf2salt") if isinstance(sp, dict) else None
        if isinstance(encoded, str) and encoded:
            salt = base64.b64decode(encoded)
        pv = sp.get("protocolVersion") if isinstance(sp, dict) else None
        if isinstance(pv, int):
            protocol_version = pv

    return LoadedVault(
        couch=couch,
        salt=salt,
        protocol_version=protocol_version,
        raw_docs=raw_docs,
    )
