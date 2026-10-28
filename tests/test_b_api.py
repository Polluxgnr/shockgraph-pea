"""Role B API tests for validation, error mapping and OpenAPI documentation."""

from dataclasses import dataclass

import pytest
from neo4j.exceptions import ServiceUnavailable


@dataclass
class RaisingClient:
    """Neo4j-like client that raises the selected service-layer exception."""

    error: Exception

    def read(self, _query, _params=None):
        """Raise instead of executing a query."""
        raise self.error

    write = read

    def close(self):
        """Match the real client's lifecycle API."""


ROLE_B_REQUESTS = [
    ("get", "/api/companies", None),
    ("get", "/api/companies/FRTEST000011", None),
    ("get", "/api/companies/FRTEST000011/exposures", None),
    ("get", "/api/factors", None),
    ("get", "/api/factors/BRENT/exposed", None),
    ("post", "/api/portfolio/xray", {"positions": [{"isin": "FRTEST000011", "amount_eur": 100}]}),
    ("post", "/api/shocks/simulate", {"shocks": [{"factor_code": "BRENT", "pct": 10}]}),
    ("get", "/api/paths?from=Factor%3ABRENT&to=Company%3AFRTEST000011", None),
]


@pytest.mark.parametrize(("method", "url", "payload"), ROLE_B_REQUESTS)
def test_unimplemented_service_is_clear_501(api_with, method, url, payload):
    """Every role B service route maps an unfinished method to the same explicit 501."""
    client = api_with(RaisingClient(NotImplementedError("B-test: pending")))

    response = client.request(method, url, json=payload)

    assert response.status_code == 501
    assert response.json() == {"detail": "Not implemented yet (B-test: pending)"}


@pytest.mark.parametrize(("method", "url", "payload"), ROLE_B_REQUESTS)
def test_database_outage_is_clear_503(api_with, method, url, payload):
    """Every role B service route maps a driver outage to a stable 503 contract."""
    client = api_with(RaisingClient(ServiceUnavailable("database offline")))

    response = client.request(method, url, json=payload)

    assert response.status_code == 503
    assert response.json() == {"detail": "Graph database unavailable"}


@pytest.mark.neo4j
@pytest.mark.parametrize(
    ("method", "url", "payload", "message"),
    [
        ("get", "/api/companies/FR0000000000", None, "Unknown company"),
        ("get", "/api/companies/FR0000000000/exposures", None, "Unknown company"),
        ("get", "/api/factors/UNKNOWN/exposed", None, "Unknown factor"),
        ("post", "/api/shocks/simulate", {"shocks": [{"factor_code": "UNKNOWN", "pct": 10}]}, "Unknown factor"),
        (
            "get",
            "/api/paths?from=Factor%3AGOLD&to=Company%3AFRTEST000011",
            None,
            "No path",
        ),
    ],
)
def test_not_found_responses_identify_the_missing_resource(api, method, url, payload, message):
    """Applicable routes return a useful 404 rather than an empty result or server error."""
    response = api.request(method, url, json=payload)

    assert response.status_code == 404
    assert message in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("get", "/api/companies?q=x", None),
        ("get", "/api/companies/not-an-isin", None),
        ("get", "/api/companies/not-an-isin/exposures", None),
        ("get", "/api/factors/X/exposed", None),
        ("post", "/api/portfolio/xray", {"positions": []}),
        ("post", "/api/shocks/simulate", {"shocks": []}),
        ("get", "/api/paths?from=bad&to=Company%3AFRTEST000011", None),
    ],
)
def test_validation_responses_name_invalid_fields(api_fake, method, url, payload):
    """Invalid requests share a concise 422 body with field-level details."""
    response = api_fake.request(method, url, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Request validation failed"
    assert body["errors"]
    assert all(error["field"] and error["message"] for error in body["errors"])


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("get", f"/api/companies?q={'x' * 81}", None),
        (
            "post",
            "/api/portfolio/xray",
            {"positions": [{"isin": "FRTEST000011", "amount_eur": 1}] * 51},
        ),
        (
            "post",
            "/api/shocks/simulate",
            {"shocks": [{"factor_code": "BRENT", "pct": 1}] * 6},
        ),
        (
            "post",
            "/api/shocks/simulate",
            {
                "shocks": [{"factor_code": "BRENT", "pct": 1}],
                "positions": [{"isin": "FRTEST000011", "amount_eur": 1}] * 51,
            },
        ),
    ],
)
def test_declared_request_size_limits_are_enforced(api_fake, method, url, payload):
    """Long text and oversized business collections are rejected before service execution."""
    response = api_fake.request(method, url, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "Request validation failed"


def test_openapi_documents_examples_and_common_failures(api_fake):
    """Swagger exposes useful bodies and the 422, 501 and 503 contracts for each service route."""
    schema = api_fake.get("/openapi.json").json()
    paths = schema["paths"]

    xray_body = paths["/api/portfolio/xray"]["post"]["requestBody"]["content"]["application/json"]
    shock_body = paths["/api/shocks/simulate"]["post"]["requestBody"]["content"]["application/json"]
    assert "stock-and-fund" in xray_body["examples"]
    assert "brent-portfolio" in shock_body["examples"]

    operations = [
        paths["/api/companies"]["get"],
        paths["/api/companies/{isin}"]["get"],
        paths["/api/companies/{isin}/exposures"]["get"],
        paths["/api/factors"]["get"],
        paths["/api/factors/{code}/exposed"]["get"],
        paths["/api/portfolio/xray"]["post"],
        paths["/api/shocks/simulate"]["post"],
        paths["/api/paths"]["get"],
    ]
    for operation in operations:
        for status in ("422", "501", "503"):
            assert operation["responses"][status]["description"]
