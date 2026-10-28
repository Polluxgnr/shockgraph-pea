"""Tests for the role B portfolio look-through."""

import pytest

from app.models.portfolio import PortfolioRequest, Position
from app.services.portfolio import PortfolioService


def fixture_request(*extra: Position) -> PortfolioRequest:
    """Build the fixture portfolio used by the role contract."""
    return PortfolioRequest(
        positions=[
            Position(isin="FRFUND000011", amount_eur=1000.0),
            Position(isin="FRTEST000011", amount_eur=500.0),
            *extra,
        ]
    )


@pytest.mark.neo4j
def test_xray_fixture_portfolio(neo4j_client) -> None:
    """Synthetic fund look-through matches every documented aggregate."""
    request = PortfolioRequest(
        positions=[
            Position(isin="FRFUND000029", amount_eur=1000.0),
            Position(isin="FRTEST000011", amount_eur=500.0),
        ]
    )
    result = PortfolioService(neo4j_client).xray(request)

    assert result.total_eur == 1500.0
    assert [(line.label, line.amount_eur) for line in result.companies] == [
        ("Petrolia SA", 650.0),
        ("Vela Software Inc", 400.0),
        ("Siliqa NV", 200.0),
        ("Maison Varenne", 150.0),
        ("Cuprum Bergbau AG", 100.0),
    ]
    assert {line.key: line.amount_eur for line in result.sectors} == {
        "ENERGY": 650.0,
        "TECH": 600.0,
        "CONS_DISC": 150.0,
        "MATERIALS": 100.0,
    }
    assert {line.key: line.amount_eur for line in result.countries} == {
        "FR": 800.0,
        "US": 400.0,
        "NL": 200.0,
        "DE": 100.0,
    }
    factors = {line.factor_code: line for line in result.factors}
    assert factors["BRENT"].weighted_beta == pytest.approx(0.2686666667)
    assert factors["BRENT"].covered_weight == pytest.approx(0.4333333333)
    assert factors["EURUSD"].weighted_beta == pytest.approx(-0.1266666667)
    assert factors["COPPER"].weighted_beta == pytest.approx(0.06)
    assert result.unknown_isins == []
    assert result.warnings == [
        "Synthetic fund FRFUND000029 uses tracked index constituents; " "its physical HOLDS basket is ignored."
    ]


@pytest.mark.neo4j
def test_xray_lists_unknown_isin(neo4j_client) -> None:
    """Unknown positions remain visible without failing known aggregation."""
    request = PortfolioRequest(
        positions=[
            Position(isin="FRTEST000011", amount_eur=500.0),
            Position(isin="XX0000000000", amount_eur=100.0),
        ]
    )
    result = PortfolioService(neo4j_client).xray(request)
    assert result.total_eur == 600.0
    assert result.unknown_isins == ["XX0000000000"]
    assert result.companies[0].amount_eur == 500.0
