"""Skeleton tests (role A): wiring, error codes, fixture integrity and the git hooks."""

import pytest

from scripts.cypher_files import split_statements
from scripts.hooks.check_commit_msg import check
from scripts.hooks.ownership import OWNERS, may_edit, owner_of

EXPECTED_NODES = {
    "Company": 9,
    "Fund": 3,
    "Index": 2,
    "Sector": 6,
    "Industry": 9,
    "Country": 4,
    "Factor": 5,
    "Article": 3,
    "Claim": 4,
}
EXPECTED_RELATIONSHIPS = {
    "TRACKS": 3,
    "HOLDS": 12,
    "CONSTITUENT": 10,
    "IN_INDUSTRY": 9,
    "PART_OF": 9,
    "HEADQUARTERED_IN": 9,
    "PRODUCES": 2,
    "CONSUMES": 4,
    "SENSITIVE_TO": 9,
    "CORRELATED_WITH": 6,
    "PREDICTED_SENSITIVITY": 1,
    "SUBJECT": 4,
    "OBJECT": 4,
    "EXTRACTED_FROM": 4,
    "MENTIONS": 7,
}


# --- API wiring (no database) -------------------------------------------------------------------


def test_health_reports_database_state(api_with):
    """Health is ok when the database answers and degraded otherwise."""
    from tests.conftest import FakeClient  # pylint: disable=import-outside-toplevel

    assert api_with(FakeClient(up=True)).get("/api/health").json()["status"] == "ok"
    body = api_with(FakeClient(up=False)).get("/api/health").json()
    assert body["status"] == "degraded" and body["neo4j"] is False


def test_swagger_lists_every_route(api_fake):
    """The OpenAPI schema exposes the contract routes."""
    paths = api_fake.get("/openapi.json").json()["paths"]
    for route in [
        "/api/health",
        "/api/companies",
        "/api/companies/{isin}",
        "/api/companies/{isin}/exposures",
        "/api/factors",
        "/api/factors/{code}/exposed",
        "/api/portfolio/xray",
        "/api/shocks/simulate",
        "/api/paths",
        "/api/network/clusters",
        "/api/network/systemic",
        "/api/network/refresh",
        "/api/companies/{isin}/similar",
        "/api/companies/{isin}/predicted-exposures",
        "/api/hypotheses",
        "/api/ask",
        "/api/agent/validate-cypher",
    ]:
        assert route in paths, route


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("get", "/api/companies/FR0000120271", None),
        ("get", "/api/factors", None),
        ("post", "/api/portfolio/xray", {"positions": [{"isin": "FR0011869353", "amount_eur": 100}]}),
        ("post", "/api/shocks/simulate", {"shocks": [{"factor_code": "BRENT", "pct": 20}]}),
        ("get", "/api/network/clusters", None),
        ("get", "/api/hypotheses", None),
    ],
)
def test_validation_errors_are_422_and_stubs_are_501(api_fake, method, url, body):
    """A valid request reaches the service: 501 until it is written, never 500."""
    response = getattr(api_fake, method)(url, json=body) if body else getattr(api_fake, method)(url)
    assert response.status_code in (200, 404, 501), response.text


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("/api/portfolio/xray", {"positions": []}),
        ("/api/portfolio/xray", {"positions": [{"isin": "not-an-isin", "amount_eur": 10}]}),
        ("/api/portfolio/xray", {"positions": [{"isin": "FR0011869353", "amount_eur": -5}]}),
        ("/api/shocks/simulate", {"shocks": [{"factor_code": "BRENT", "pct": 1000}]}),
        ("/api/shocks/simulate", {"shocks": [{"factor_code": "BRENT", "pct": 10}], "depth": 9}),
    ],
)
def test_invalid_bodies_are_rejected_with_422(api_fake, url, body):
    """Pydantic validation answers 422 before any query runs."""
    assert api_fake.post(url, json=body).status_code == 422


def test_invalid_isin_in_path_is_422(api_fake):
    """Path parameters are validated too."""
    assert api_fake.get("/api/companies/abc").status_code == 422


def test_ask_requires_api_key(api_fake, auth_headers):
    """The LLM endpoint is protected: 401 without the key, past the guard with it."""
    assert api_fake.post("/api/ask", json={"question": "Brent +20 %?"}).status_code == 401
    bad = {"X-API-Key": "wrong"}
    assert api_fake.post("/api/ask", json={"question": "Brent +20 %?"}, headers=bad).status_code == 401
    assert api_fake.post("/api/ask", json={"question": "Brent +20 %?"}, headers=auth_headers).status_code != 401


def test_refresh_requires_api_key(api_fake):
    """The GDS write endpoint is protected."""
    assert api_fake.post("/api/network/refresh").status_code == 401


# --- Cypher files and the mini graph -------------------------------------------------------------


def test_split_statements_ignores_comments_and_blank_lines():
    """The runner splits on ';' at line end and drops '//' comments."""
    script = "// header\nCREATE (a);\n\nMATCH (n)\nRETURN n;\n"
    assert split_statements(script) == ["CREATE (a)", "MATCH (n)\nRETURN n"]


@pytest.mark.neo4j
def test_mini_graph_node_counts(neo4j_client):
    """The fixture loads exactly the documented nodes."""
    rows = neo4j_client.read("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count")
    assert {row["label"]: row["count"] for row in rows} == EXPECTED_NODES


@pytest.mark.neo4j
def test_mini_graph_relationship_counts(neo4j_client):
    """The fixture loads exactly the documented relationships."""
    rows = neo4j_client.read("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count")
    assert {row["type"]: row["count"] for row in rows} == EXPECTED_RELATIONSHIPS


@pytest.mark.neo4j
def test_constraints_and_fulltext_indexes_exist(neo4j_client):
    """The contract indexes are created by scripts/constraints.cypher."""
    names = {row["name"] for row in neo4j_client.read("SHOW INDEXES YIELD name RETURN name")}
    assert {"company_isin", "factor_code", "company_name_ft", "fund_name_ft", "factor_name_ft"} <= names


@pytest.mark.neo4j
def test_unique_isin_is_enforced(neo4j_client):
    """A second company with the same ISIN is refused by the constraint."""
    with pytest.raises(Exception):
        neo4j_client.write("CREATE (:Company {isin: 'FRTEST000011', name: 'duplicate'})")


@pytest.mark.neo4j
def test_health_on_real_database(api):
    """Health is ok on the test database."""
    assert api.get("/api/health").json()["neo4j"] is True


# --- Git hooks ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["feat(api): add cursor pagination", "fix: handle empty portfolio", "Merge branch 'main' into feat/b-x"],
)
def test_commit_messages_accepted(message):
    """Conventional headers and merge commits pass."""
    assert check(message) == []


@pytest.mark.parametrize(
    "message",
    [
        "added stuff",
        "feat: " + "x" * 80,
        "feat(api): add search\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        "docs: readme\n\nGenerated with Codex",
    ],
)
def test_commit_messages_refused(message):
    """Free-form headers, long headers and tool attribution are refused."""
    assert check(message)


def test_every_file_has_one_owner():
    """No pattern is claimed by two roles."""
    seen: dict[str, str] = {}
    for role, patterns in OWNERS.items():
        for pattern in patterns:
            assert pattern not in seen, f"{pattern} owned by {seen.get(pattern)} and {role}"
            seen[pattern] = role


@pytest.mark.parametrize(
    ("role", "path", "allowed"),
    [
        ("b", "app/services/shocks.py", True),
        ("b", "app/services/gds.py", False),
        ("c", "app/agent/graph.py", True),
        ("c", "app/agent/guardrails.py", False),
        ("a", "tests/test_a_ingest.py", True),
        ("a", "tests/test_b_api.py", False),
        ("c", "requirements.txt", True),
    ],
)
def test_ownership(role, path, allowed):
    """Each role may edit its own files, its own tests and the append-only files."""
    assert may_edit(role, path) is allowed


def test_owner_of_unknown_file_is_none():
    """Files nobody owns must be added to ownership.py by the lead."""
    assert owner_of("random/file.txt") is None
