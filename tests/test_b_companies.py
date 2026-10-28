"""Unit tests for role B company and factor services."""

from typing import Any

import pytest

from app.services.companies import CompanyService, FactorService


class FakeClient:
    """Record parameterised reads and return queued rows."""

    def __init__(self, *responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return the next response."""
        self.calls.append((query, params or {}))
        return self.responses.pop(0)


def company_row(isin: str, name: str, cap: float) -> dict[str, Any]:
    """Build a company search row."""
    return {
        "isin": isin,
        "name": name,
        "ticker": None,
        "sector": "Energy",
        "country": "FR",
        "pea_eligible": True,
        "cap": cap,
    }


def test_search_escapes_fulltext_and_returns_cursor_second_page() -> None:
    """Search fetches one extra row and resumes after the opaque sort key."""
    first_client = FakeClient(
        [
            company_row("FRTEST000011", "Petrolia SA", 120.0),
            company_row("FRTEST000029", "AeroLys", 4.0),
        ]
    )
    first = CompanyService(first_client).search(query="petrol+(sa)", pea_only=True, limit=1)

    assert [item.name for item in first.items] == ["Petrolia SA"]
    assert first.next_cursor is not None
    assert first_client.calls[0][1]["query"] == r"petrol\+\(sa\)*"
    assert "SKIP" not in first_client.calls[0][0].upper()
    assert first_client.calls[0][1]["fetch_limit"] == 2

    second_client = FakeClient([company_row("FRTEST000029", "AeroLys", 4.0)])
    second = CompanyService(second_client).search(limit=1, cursor=first.next_cursor)
    assert [item.name for item in second.items] == ["AeroLys"]
    assert second.next_cursor is None
    assert second_client.calls[0][1]["cursor_cap"] == 120.0
    assert second_client.calls[0][1]["cursor_isin"] == "FRTEST000011"


def test_search_rejects_invalid_cursor() -> None:
    """Malformed pagination state is rejected before a database query."""
    with pytest.raises(ValueError, match="Invalid company pagination cursor"):
        CompanyService(FakeClient()).search(cursor="not-json")


def test_get_company_maps_detail() -> None:
    """Company detail includes classification and index memberships."""
    client = FakeClient(
        [
            {
                "isin": "FRTEST000011",
                "name": "Petrolia SA",
                "ticker": "PTL.PA",
                "sector": "Energy",
                "industry": "Oil & Gas",
                "country": "FR",
                "pea_eligible": True,
                "lei": None,
                "market_cap_eur": 120e9,
                "indexes": ["IDX_FR40"],
            }
        ]
    )
    company = CompanyService(client).get("FRTEST000011")
    assert company is not None
    assert company.indexes == ["IDX_FR40"]
    assert CompanyService(FakeClient([])).get("FRTEST000099") is None


def test_exposures_keep_fact_and_estimate_distinct() -> None:
    """A factor may have both a declared fact and a statistical estimate."""
    common = {
        "company_isin": "FRTEST000011",
        "company_name": "Petrolia SA",
        "factor_code": "BRENT",
        "factor_name": "Brent crude oil",
    }
    client = FakeClient(
        [
            {**common, "relation": "SENSITIVE_TO", "truth": "estimate", "beta": 0.62, "r2": 0.48},
            {**common, "relation": "PRODUCES", "truth": "fact", "beta": None, "r2": None, "source": "fixture"},
        ]
    )
    results = CompanyService(client).exposures("FRTEST000011", min_r2=0.1)
    assert {(item.relation, item.truth) for item in results} == {("SENSITIVE_TO", "estimate"), ("PRODUCES", "fact")}
    assert client.calls[0][1] == {"isin": "FRTEST000011", "min_r2": 0.1}


def test_resolve_company_uses_fulltext_limit() -> None:
    """Entity resolution uses escaped prefix matching."""
    client = FakeClient([company_row("FRTEST000011", "Petrolia SA", 120.0)])
    results = CompanyService(client).resolve("petrol", limit=3)
    assert results[0].isin == "FRTEST000011"
    assert client.calls[0][1] == {"query": "petrol*", "limit": 3}


def test_factor_list_exposed_unknown_and_resolve() -> None:
    """Factor methods map rows, order exposure, and distinguish unknown codes."""
    factors = [{"code": "BRENT", "name": "Brent", "kind": "commodity", "unit": "USD"}]
    assert FactorService(FakeClient(factors)).list_factors()[0].code == "BRENT"

    common = {
        "factor_code": "BRENT",
        "factor_name": "Brent",
        "relation": "SENSITIVE_TO",
        "truth": "estimate",
        "company_isin": "FRTEST000011",
        "company_name": "Petrolia SA",
        "beta": 0.62,
        "r2": 0.48,
    }
    exposed_client = FakeClient([{"code": "BRENT"}], [common])
    exposed = FactorService(exposed_client).exposed("BRENT")
    assert exposed is not None and exposed[0].beta == 0.62
    assert "abs(rel.beta) DESC" in exposed_client.calls[1][0]
    assert FactorService(FakeClient([])).exposed("UNKNOWN") is None

    resolved = FactorService(FakeClient(factors)).resolve("brent")
    assert resolved[0].kind == "commodity"


@pytest.mark.neo4j
def test_company_search_on_mini_graph(neo4j_client) -> None:
    """The mini graph validates full-text, filters, ordering and page two."""
    service = CompanyService(neo4j_client)
    assert [item.name for item in service.search(query="petrol").items] == ["Petrolia SA"]

    first = service.search(pea_only=True, limit=3)
    assert [item.name for item in first.items] == ["Maison Varenne", "Siliqa NV", "Petrolia SA"]
    assert first.next_cursor is not None
    second = service.search(pea_only=True, limit=3, cursor=first.next_cursor)
    assert [item.name for item in second.items] == [
        "Autoroute Motoren AG",
        "Banque Orsay",
        "Cuprum Bergbau AG",
    ]


@pytest.mark.neo4j
def test_company_details_and_exposures_on_mini_graph(neo4j_client) -> None:
    """Petrolia has both its estimated and industry-declared BRENT links."""
    service = CompanyService(neo4j_client)
    company = service.get("FRTEST000011")
    assert company is not None
    assert company.country == "FR"
    assert company.indexes == ["IDX_FR40", "IDX_WORLD"]
    exposures = service.exposures("FRTEST000011", min_r2=0.1)
    assert {(item.factor_code, item.relation, item.truth) for item in exposures} == {
        ("BRENT", "PRODUCES", "fact"),
        ("BRENT", "SENSITIVE_TO", "estimate"),
    }


@pytest.mark.neo4j
def test_factor_exposure_on_mini_graph(neo4j_client) -> None:
    """BRENT exposure honors r2 and absolute-beta ordering."""
    service = FactorService(neo4j_client)
    exposed = service.exposed("BRENT", min_r2=0.1)
    assert exposed is not None
    assert [(item.company_name, item.beta) for item in exposed] == [
        ("Petrolia SA", 0.62),
        ("AeroLys", -0.41),
    ]
    all_exposed = service.exposed("BRENT", min_r2=0.0)
    assert all_exposed is not None
    assert [item.company_name for item in all_exposed] == ["Petrolia SA", "AeroLys", "Helios Chimie"]
    assert service.exposed("UNKNOWN") is None
