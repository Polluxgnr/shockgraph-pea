"""Role B smoke tests for the optional full Docker Compose stack."""

import os
import time
from pathlib import Path

import httpx
import pytest
import yaml

pytestmark = pytest.mark.containers

BASE_URL = os.environ.get("CONTAINER_BASE_URL", "http://localhost").rstrip("/")
ROOT = Path(__file__).parent.parent


def test_compose_keeps_api_private_and_waits_for_dependencies():
    """The declarative stack exposes only the proxy and waits for GDS and Ollama readiness."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "ports" not in services["api"]
    assert services["api"]["expose"] == ["8000"]
    assert services["api"]["depends_on"]["neo4j"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["ollama"]["condition"] == "service_healthy"
    assert "gds.version()" in services["neo4j"]["healthcheck"]["test"][-1]
    assert "HEALTHCHECK_PASSWORD" in services["neo4j"]["environment"]
    assert "NEO4J_PASSWORD" in services["api"]["environment"]
    assert "API_KEY" in services["api"]["environment"]


def test_nginx_enforces_body_auth_route_and_rate_limits():
    """The public proxy bounds bodies and rate-limits the protected slow endpoint."""
    config = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "client_max_body_size 64k" in config
    assert "location = /api/ask" in config
    assert "limit_req zone=ask" in config
    assert "limit_req_status 429" in config


def test_publish_workflow_targets_upstream_ghcr_package():
    """A merge to main publishes immutable and latest API image tags to upstream GHCR."""
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "branches: [main]" in workflow
    assert "ghcr.io/polluxgnr/shockgraph-api:latest" in workflow
    assert "ghcr.io/polluxgnr/shockgraph-api:${{ github.sha }}" in workflow


@pytest.fixture(scope="module")
def stack_client():
    """Return an HTTP client or skip when the reverse-proxied stack is not running."""
    client = httpx.Client(base_url=BASE_URL, timeout=5)
    try:
        response = client.get("/api/health")
    except httpx.HTTPError:
        client.close()
        pytest.skip(f"container stack is not available at {BASE_URL} (make docker-run)")
    if response.status_code != 200:
        client.close()
        pytest.skip(f"container stack health returned HTTP {response.status_code}")
    yield client
    client.close()


def test_proxy_reports_healthy_neo4j_and_gds(stack_client):
    """The public request crosses Nginx and proves both Neo4j and GDS are ready."""
    response = stack_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["neo4j"] is True
    assert response.json()["gds"] is True


def test_ask_requires_api_key_through_proxy(stack_client):
    """Nginx forwards the protected route and the API rejects a missing key."""
    response = stack_client.post("/api/ask", json={"question": "What moves Petrolia?"})

    assert response.status_code == 401
    assert "X-API-Key" in response.json()["detail"]


def test_proxy_rate_limits_repeated_agent_requests(stack_client):
    """The slow LLM endpoint eventually returns 429 under a short burst."""
    statuses = []
    for _ in range(12):
        statuses.append(stack_client.post("/api/ask", json={"question": "test"}).status_code)
        if 429 in statuses:
            break
        time.sleep(0.02)

    assert 429 in statuses
