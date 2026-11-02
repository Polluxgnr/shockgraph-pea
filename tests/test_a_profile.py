"""Tests of the PROFILE / EXPLAIN helpers of Neo4jClient (role A), used by the benchmarks (B6)."""

import pytest
from neo4j import RoutingControl
from neo4j.exceptions import ClientError

from app.database.neo4j import Neo4jClient, plan_operators, plan_total

PLAN = {
    "operatorType": "ProduceResults@neo4j",
    "dbHits": 0,
    "children": [{"operatorType": "NodeUniqueIndexSeek@neo4j", "dbHits": 2, "children": []}],
}


def test_plan_helpers_flatten_operators_and_sum_db_hits():
    """Depth-first operator names without the planner suffix, total db hits."""
    assert plan_operators(PLAN) == ["ProduceResults", "NodeUniqueIndexSeek"]
    assert plan_total(PLAN, "dbHits") == 2
    assert plan_operators(None) == [] and plan_total({}, "dbHits") == 0


@pytest.mark.neo4j
def test_company_lookup_uses_the_unique_index(neo4j_client):
    """EXPLAIN shows an index seek on Company.isin, and PROFILE counts db hits and rows."""
    query = "MATCH (c:Company {isin: $isin}) RETURN c.name AS name"
    assert "NodeUniqueIndexSeek" in neo4j_client.explain(query, {"isin": "FRTEST000011"})
    profile = neo4j_client.profile(query, {"isin": "FRTEST000011"})
    assert profile["rows"] == 1 and profile["db_hits"] > 0 and "NodeUniqueIndexSeek" in profile["operators"]


@pytest.mark.neo4j
def test_market_cap_index_exists(neo4j_client):
    """The range index used by the paginated search is created by scripts/constraints.cypher."""
    names = {row["name"] for row in neo4j_client.read("SHOW INDEXES YIELD name RETURN name")}
    assert "company_market_cap" in names


@pytest.mark.neo4j
def test_market_cap_index_serves_the_order(neo4j_client):
    """Sorting on the raw property reads the range index in order: no full Sort over every company."""
    query = (
        "MATCH (c:Company) WHERE c.market_cap_eur IS NOT NULL "
        "RETURN c.isin AS isin ORDER BY c.market_cap_eur DESC, c.isin LIMIT $limit"
    )
    operators = neo4j_client.explain(query, {"limit": 3})
    assert "NodeIndexScan" in operators and "Sort" not in operators


@pytest.mark.neo4j
def test_profile_refuses_writes(neo4j_client):
    """PROFILE is routed as a read: a CREATE is refused by Neo4j and leaves nothing behind."""
    with pytest.raises(ClientError):
        neo4j_client.profile("CREATE (n:Company {isin: 'ZZPROBE00040'}) RETURN n.isin AS isin")
    assert not neo4j_client.read("MATCH (n:Company {isin: 'ZZPROBE00040'}) RETURN n")


@pytest.mark.neo4j
def test_read_with_timeout_returns_rows(neo4j_client):
    """Within the time budget it behaves like `read`."""
    rows = neo4j_client.read_with_timeout(
        "MATCH (c:Company {isin: $isin}) RETURN c.name AS name", {"isin": "FRTEST000011"}
    )
    assert len(rows) == 1


@pytest.mark.neo4j
def test_read_with_timeout_stops_heavy_work(neo4j_client):
    """A query that passes the guardrail but does a lot of work is stopped by the server, not by the client."""
    with pytest.raises(ClientError):
        neo4j_client.read_with_timeout("UNWIND range(1, 50000000) AS x RETURN count(x) AS n LIMIT 1", seconds=0.05)


def test_read_with_timeout_sends_the_timeout_to_the_server():
    """The timeout travels with the query (server side), and the query is routed as a read."""
    calls = []

    class FakeDriver:  # pylint: disable=too-few-public-methods
        """Records what execute_query receives."""

        def execute_query(self, query, **kwargs):
            """Return no record, keep the call."""
            calls.append((query, kwargs))
            return [], None, None

    client = Neo4jClient("bolt://localhost:1", "neo4j", "unused")
    client._driver = FakeDriver()  # pylint: disable=protected-access
    assert client.read_with_timeout("RETURN 1 AS one", seconds=2.5) == []
    query, kwargs = calls[0]
    assert query.timeout == 2.5 and query.text == "RETURN 1 AS one"
    assert kwargs["routing_"] == RoutingControl.READ
