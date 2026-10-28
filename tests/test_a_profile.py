"""Tests of the PROFILE / EXPLAIN helpers of Neo4jClient (role A), used by the benchmarks (B6)."""

import pytest

from app.database.neo4j import plan_operators, plan_total

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
