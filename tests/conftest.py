"""Shared fixtures (role A).

- `neo4j_client`: the disposable test database (make test-db, port 7688), wiped and loaded with the
  mini graph once per session. Tests marked `neo4j` are skipped when it is not running.
- `api`: a FastAPI TestClient whose Neo4j client is the test database (or a fake when absent).
- `fake_client`: an in-memory stand-in to unit-test code that only needs `read`/`write` calls.
"""

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.database.neo4j import Neo4jClient
from scripts.cypher_files import run_file

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")
TEST_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")
TEST_API_KEY = "test-api-key"

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ["API_KEY"] = TEST_API_KEY


class FakeClient:
    """Records queries and returns canned rows; `ping` says whether the 'database' is up."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, up: bool = True) -> None:
        self.rows = rows or []
        self.up = up
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return the canned rows."""
        self.queries.append((query, params or {}))
        return self.rows

    write = read

    def ping(self) -> bool:
        """Pretend the database answers (or not)."""
        return self.up

    def has_gds(self) -> bool:
        """Pretend GDS is installed when up."""
        return self.up

    def close(self) -> None:
        """Nothing to close."""


def load_mini_graph(client: Neo4jClient) -> None:
    """Wipe the test database, apply the constraints and load the mini graph."""
    client.write("MATCH (n) DETACH DELETE n")
    run_file(client, ROOT / "scripts" / "constraints.cypher")
    client.write("CALL db.awaitIndexes(60)")
    run_file(client, FIXTURES / "mini_graph.cypher")


@pytest.fixture(scope="session")
def neo4j_client():
    """The test database loaded with the mini graph, or skip the test when it is not running."""
    client = Neo4jClient(TEST_URI, "neo4j", TEST_PASSWORD)
    if not client.ping():
        client.close()
        pytest.skip(f"test database not running at {TEST_URI} (make test-db)")
    load_mini_graph(client)
    yield client
    client.close()


@pytest.fixture
def fake_client() -> FakeClient:
    """A fresh fake client."""
    return FakeClient()


@pytest.fixture
def api_with(request):
    """Factory: TestClient wired to the given Neo4j-like client."""
    from app.main import app  # pylint: disable=import-outside-toplevel

    clients: list[TestClient] = []

    def make(client) -> TestClient:
        app.state.neo4j = client
        test_client = TestClient(app)
        clients.append(test_client)
        return test_client

    yield make
    for test_client in clients:
        test_client.close()
    if hasattr(app.state, "neo4j"):
        del app.state.neo4j
    del request


@pytest.fixture
def api(api_with, neo4j_client) -> TestClient:
    """TestClient on the mini graph (skipped without the test database)."""
    return api_with(neo4j_client)


@pytest.fixture
def api_fake(api_with, fake_client) -> TestClient:
    """TestClient on a fake client: for routing, validation and error-handling tests."""
    return api_with(fake_client)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Headers accepted by the API-key protected endpoints in tests."""
    return {"X-API-Key": TEST_API_KEY}
