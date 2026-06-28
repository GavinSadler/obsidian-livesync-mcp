"""Live integration tests against a real CouchDB (opt-in).

These are **skipped unless ``COUCHDB_URL`` is set**. When enabled they load the
exported fixtures into throwaway per-vault databases and drive the real
``CouchDBClient`` + ``NoteRepository`` over HTTP — exercising the network
client, the write/conflict paths, the ``_local`` salt fetch, and full
write→read-back round-trips that the in-memory ``FakeCouchDBClient`` can't
reach.

Run locally against the author's CouchDB container::

    # from repo root — start + configure CouchDB (couchdb:3.5.0 on :5989)
    username=admin password=testpassword bash src/apps/cli/util/couchdb-start.sh
    hostname=http://127.0.0.1:5989/ username=admin password=testpassword \\
        dbname=livesync-test-db-ci node=_local bash src/apps/cli/util/couchdb-init.sh

    cd mcp_server
    COUCHDB_URL=http://127.0.0.1:5989 COUCHDB_USERNAME=admin \\
        COUCHDB_PASSWORD=testpassword uv run pytest tests/test_integration_live.py -v

The fixtures are loaded into databases named ``mcp-live-<vault>`` (dropped +
recreated each run), so they never touch a real vault.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient
from obsidian_livesync_mcp.livesync import encryption
from obsidian_livesync_mcp.livesync.chunks import hash_chunk, split_pieces_rabin_karp
from obsidian_livesync_mcp.livesync.notes import NoteRepository, fetch_pbkdf2_salt
from scripts.load_fixture import load_fixture_into_couch
from tests.fixture_loader import available_vaults

COUCHDB_URL = os.environ.get("COUCHDB_URL")
if not COUCHDB_URL:
    pytest.skip(
        "COUCHDB_URL not set; live CouchDB integration tests skipped "
        "(see the module docstring to run them)",
        allow_module_level=True,
    )

USERNAME = os.environ.get("COUCHDB_USERNAME")
PASSWORD = os.environ.get("COUCHDB_PASSWORD")
VERIFY_TLS = os.environ.get("COUCHDB_VERIFY_TLS", "true").lower() not in ("0", "false", "no")
PASSPHRASE = "ThisIsMyObsidianNotebook1234"

# README is present (under varying case) in every fixture vault.
README_PATH = "README.md"


@dataclass
class LiveVault:
    name: str
    client: CouchDBClient
    repo: NoteRepository


def _drop_db(database: str) -> None:
    with httpx.Client(
        auth=(USERNAME, PASSWORD) if USERNAME and PASSWORD else None, verify=VERIFY_TLS
    ) as c:
        c.delete(f"{str(COUCHDB_URL).rstrip('/')}/{database}")


@pytest.fixture(params=available_vaults())
async def live_vault(request: pytest.FixtureRequest) -> AsyncIterator[LiveVault]:
    """Load one fixture vault into a throwaway DB and yield a live repo."""
    vault = request.param
    database = f"mcp-live-{vault}"
    load_fixture_into_couch(
        base_url=str(COUCHDB_URL),
        database=database,
        vault=vault,
        username=USERNAME,
        password=PASSWORD,
        verify_tls=VERIFY_TLS,
        reset=True,
    )
    client = CouchDBClient(
        str(COUCHDB_URL), database, username=USERNAME, password=PASSWORD, verify_tls=VERIFY_TLS
    )
    encrypted = vault in ("encrypted", "obfuscated")
    # Fetch the salt the same way the server does at startup — this also proves
    # the `_local` sync-parameters doc round-trips through the real client.
    salt = await fetch_pbkdf2_salt(client) if encrypted else None
    repo = NoteRepository(
        client,
        passphrase=PASSPHRASE if encrypted else None,
        pbkdf2_salt=salt,
        obfuscate_paths=(vault == "obfuscated"),
        case_sensitive=False,
    )
    try:
        yield LiveVault(name=vault, client=client, repo=repo)
    finally:
        await client.close()
        _drop_db(database)


# --------------------------------------------------------------------------
# Reads (all vaults) — exercises get / bulk_get / all_docs over real HTTP
# --------------------------------------------------------------------------


async def test_live_read_readme(live_vault: LiveVault) -> None:
    note = await live_vault.repo.read(README_PATH)
    assert note is not None, f"[{live_vault.name}] README did not resolve"
    assert note.path == "README.md"
    assert "Fixture Vault Documentation" in note.content


async def test_live_salt_fetched_for_encrypted_vaults(live_vault: LiveVault) -> None:
    """The `_local` sync-parameters doc must be readable through the real client."""
    salt = await fetch_pbkdf2_salt(live_vault.client)
    if live_vault.name == "plain":
        return  # plain vaults may or may not carry a salt; nothing to assert
    assert salt is not None, f"[{live_vault.name}] PBKDF2 salt not fetchable from _local doc"
    assert isinstance(salt, bytes) and len(salt) == 32


async def test_live_list_paths_surfaces_real_notes(live_vault: LiveVault) -> None:
    listed = await live_vault.repo.list_paths()
    paths = {item["path"] for item in listed}
    assert "README.md" in paths
    # No chunk / internal / obfuscated-blob leakage.
    assert not any(p.startswith(("h:", "_", "f:", "/\\:")) for p in paths)
    assert "%=" not in "".join(paths)


# --------------------------------------------------------------------------
# Writes (all vaults) — exercises put / bulk_docs and the write path
#
# plain / encrypted store metadata in plaintext top-level fields; obfuscated
# stores it in the encrypted `/\:%=` `path` blob (Property Encryption).
# --------------------------------------------------------------------------


async def test_live_write_read_update_delete_roundtrip(live_vault: LiveVault) -> None:
    repo = live_vault.repo
    path = "live-roundtrip.md"

    created = await repo.create(path, "# Live\n\nfirst version\n")
    assert created.content == "# Live\n\nfirst version\n"

    back = await repo.read(path)
    assert back is not None and back.content == "# Live\n\nfirst version\n"

    await repo.update(path, "# Live\n\nsecond version\n")
    back2 = await repo.read(path)
    assert back2 is not None and back2.content == "# Live\n\nsecond version\n"

    assert await repo.delete(path) is True
    assert await repo.read(path) is None


async def test_live_write_reproduces_plugin_chunk_ids(live_vault: LiveVault) -> None:
    """A write of the README content must produce the plugin's exact chunk IDs.

    This is the write-side compatibility signal: re-splitting + hashing the
    content yields the same ``children`` the plugin stored, so our writes dedup
    against existing chunks instead of forking the vault. (plain/encrypted only:
    obfuscated keeps children inside the encrypted blob — see the next test.)
    """
    if live_vault.name == "obfuscated":
        pytest.skip("obfuscated stores children in the encrypted metadata blob")
    repo = live_vault.repo

    source = await repo.read(README_PATH)
    assert source is not None
    original = await live_vault.client.get("readme.md")
    assert original is not None

    await repo.create("live-readme-copy.md", source.content)
    written = await live_vault.client.get(repo._path_to_id("live-readme-copy.md"))
    assert written is not None
    # Same content → identical content-addressed chunk IDs as the plugin's readme.
    assert written["children"] == original["children"]

    # And our standalone splitter agrees with what the write path stored.
    enc = bool(repo._passphrase)
    expected = [
        hash_chunk(p, encrypted=enc, hashed_passphrase=repo._hashed_passphrase)
        for p in split_pieces_rabin_karp(source.content)
    ]
    assert written["children"] == expected


async def test_live_obfuscated_write_produces_property_encrypted_doc(
    live_vault: LiveVault,
) -> None:
    """obfuscated: a write produces the f: id + encrypted metadata blob shape the
    plugin uses, and the blob decrypts to metadata whose chunk IDs reproduce."""
    if live_vault.name != "obfuscated":
        pytest.skip("Property-Encryption write shape only applies to obfuscated vaults")
    repo = live_vault.repo
    content = "# Obfuscated Write\n\n中文 🚀 — encrypted-metadata round trip.\n"

    await repo.create("live-obf-write.md", content)
    raw = await live_vault.client.get(repo._path_to_id("live-obf-write.md"))
    assert raw is not None
    # Plugin on-disk shape: encrypted blob in `path`, zeroed top-level fields.
    assert raw["path"].startswith("/\\:") and "%=" in raw["path"]
    assert raw["children"] == [] and raw["mtime"] == 0 and raw["size"] == 0

    salt = await fetch_pbkdf2_salt(live_vault.client)
    assert salt is not None
    blob = raw["path"]
    meta = json.loads(encryption.decrypt(blob[blob.index("%=") :], PASSPHRASE, salt))
    assert meta["path"] == "live-obf-write.md"
    assert meta["size"] == len(content.encode("utf-8"))
    assert meta["children"] == [
        hash_chunk(p, encrypted=True, hashed_passphrase=repo._hashed_passphrase)
        for p in split_pieces_rabin_karp(content)
    ]

    # And it reads back transparently through the repository.
    back = await repo.read("live-obf-write.md")
    assert back is not None and back.content == content
