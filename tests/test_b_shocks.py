"""Role B tests for direct shocks, supply-chain hypotheses and explainable paths."""

import pytest

from app.models.portfolio import Position
from app.models.shocks import Shock, ShockRequest
from app.services.shocks import ShockService


@pytest.mark.neo4j
def test_brent_direct_impacts_keep_estimates_and_direction_facts(neo4j_client):
    """A low-r2 sensitivity falls back to the declared direction-only fact."""
    result = ShockService(neo4j_client).simulate(
        ShockRequest(shocks=[Shock(factor_code="BRENT", pct=20)], depth=1, min_r2=0.1)
    )

    lines = {line.isin: line for line in result.direct}
    assert set(lines) == {"FRTEST000011", "FRTEST000029", "FRTEST000086"}
    assert lines["FRTEST000011"].impact_pct == pytest.approx(12.4)
    assert lines["FRTEST000011"].truth == "estimate"
    assert lines["FRTEST000029"].impact_pct == pytest.approx(-8.2)
    assert lines["FRTEST000086"].impact_pct is None
    assert lines["FRTEST000086"].truth == "fact"
    assert result.indirect == []


@pytest.mark.neo4j
def test_eurusd_indirect_excludes_rejected_claim(neo4j_client):
    """Only a non-rejected claim propagates a direct currency exposure."""
    result = ShockService(neo4j_client).simulate(ShockRequest(shocks=[Shock(factor_code="eurusd", pct=-5)], depth=2))

    direct = {line.isin: line for line in result.direct}
    assert direct["FRTEST000037"].impact_pct == pytest.approx(4.0)
    assert direct["NLTEST000045"].impact_pct == pytest.approx(1.75)
    assert [line.isin for line in result.indirect] == ["DETEST000060"]
    assert result.indirect[0].truth == "hypothesis"
    assert "Claim:c3" in result.indirect[0].path
    assert all(line.isin != "USTEST000094" for line in result.direct + result.indirect)


@pytest.mark.neo4j
def test_portfolio_shock_reuses_tracked_index_look_through(neo4j_client):
    """Euro impact uses the B2 economic exposure, not a synthetic fund's basket."""
    result = ShockService(neo4j_client).simulate(
        ShockRequest(
            shocks=[Shock(factor_code="BRENT", pct=20)],
            positions=[
                Position(isin="FRFUND000029", amount_eur=1000),
                Position(isin="FRTEST000011", amount_eur=500),
            ],
            depth=1,
        )
    )

    assert result.portfolio_impact_eur == pytest.approx(80.60)
    assert result.portfolio_impact_pct == pytest.approx(5.3733333333)
    petrolia = next(line for line in result.direct if line.isin == "FRTEST000011")
    assert petrolia.impact_eur == pytest.approx(80.60)
    assert any("synthetic" in warning.lower() for warning in result.warnings)


@pytest.mark.neo4j
def test_unknown_factor_raises_key_error(neo4j_client):
    """An unknown factor is distinguishable from a known factor with no links."""
    with pytest.raises(KeyError, match="UNKNOWN"):
        ShockService(neo4j_client).simulate(ShockRequest(shocks=[Shock(factor_code="UNKNOWN", pct=10)], depth=1))

    gold = ShockService(neo4j_client).simulate(ShockRequest(shocks=[Shock(factor_code="GOLD", pct=10)], depth=1))
    assert gold.direct == []


@pytest.mark.neo4j
def test_shortest_path_labels_truth_and_returns_none(neo4j_client):
    """The shortest direct sensitivity is an estimate and disconnected GOLD has no path."""
    service = ShockService(neo4j_client)
    result = service.path("Factor:BRENT", "Company:FRTEST000029", max_hops=4)

    assert result is not None
    assert result.hops == 1
    assert result.nodes == ["Factor:BRENT", "Company:FRTEST000029"]
    assert result.relations == ["SENSITIVE_TO"]
    assert result.truth_levels == ["estimate"]
    assert service.path("Factor:GOLD", "Company:FRTEST000029", max_hops=6) is None


@pytest.mark.parametrize("bound", [True, 0, 7, 1.5, "4"])
def test_path_rejects_non_integer_or_out_of_range_bound(fake_client, bound):
    """Only a genuine integer from one to six can enter the Cypher pattern."""
    with pytest.raises(ValueError, match=r"int in \[1, 6\]"):
        ShockService(fake_client).path("Factor:BRENT", "Company:FRTEST000029", bound)
    assert fake_client.queries == []


def test_path_parameterises_reference_values(fake_client):
    """Reference keys remain parameters while the validated bound alone enters query text."""
    ShockService(fake_client).path("Factor:BRENT", "Company:FRTEST000029", 3)

    query, params = fake_client.queries[0]
    assert "[*..3]" in query
    assert "BRENT" not in query
    assert "FRTEST000029" not in query
    assert params["from_key"] == "BRENT"
    assert params["to_key"] == "FRTEST000029"


def test_path_rejects_unsupported_label(fake_client):
    """Labels are allow-listed before the database is queried."""
    with pytest.raises(ValueError, match="unsupported reference label"):
        ShockService(fake_client).path("User:anything", "Factor:BRENT")
    assert fake_client.queries == []
