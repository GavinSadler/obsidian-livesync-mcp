"""Shared pytest fixtures."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from obsidian_livesync_mcp.couchdb import CouchDBClient


@dataclass
class LiveCouch:
    """Holds a CouchDBClient wired to a live (container) CouchDB instance."""

    client: CouchDBClient


@pytest.fixture
def sample_passphrase() -> str:
    return "correct horse battery staple"


@pytest.fixture
def sample_note_content() -> str:
    return (
        "# Hello\n\n"
        "This is a test note.\n\n"
        "## Section A\n\n"
        "Some content under A.\n\n"
        "## Section B\n\n"
        "Some content under B.\n"
    )


@pytest.fixture
async def live_couch() -> LiveCouch:
    """Spin up a CouchDB 3.3 container and return a connected LiveCouch.

    Requires Docker. Skip automatically when Docker is unavailable or the
    ``live`` mark is not selected.
    """
    try:
        import docker as _docker

        _docker.from_env().ping()
    except Exception:
        pytest.skip("Docker not available — skipping live CouchDB tests")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    DB = "testdb"
    USER = "admin"
    PASSWORD = "password"

    with (
        DockerContainer("couchdb:3.3")
        .with_env("COUCHDB_USER", USER)
        .with_env("COUCHDB_PASSWORD", PASSWORD)
        .with_exposed_ports(5984) as container
    ):
        wait_for_logs(container, "Apache CouchDB has started", timeout=30)
        port = container.get_exposed_port(5984)
        base_url = f"http://localhost:{port}"

        # Bootstrap single-node mode.
        import httpx

        async with httpx.AsyncClient(auth=(USER, PASSWORD)) as http:
            await http.put(f"{base_url}/_users")
            await http.put(f"{base_url}/_replicator")
            await http.put(f"{base_url}/{DB}")
            await http.put(
                f"{base_url}/_node/nonode@nohost/_config/chttpd/bind_address",
                content=b'"0.0.0.0"',
            )

        client = CouchDBClient(base_url, DB, username=USER, password=PASSWORD)
        yield LiveCouch(client=client)
        await client.close()
