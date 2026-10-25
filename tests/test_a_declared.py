"""Tests of the declared exposures (role A, A2). No network: Wikidata rows are built in memory."""

import csv
from pathlib import Path

import pytest

from scripts.ingest import declared
from scripts.ingest.sources.wikidata import build_query, entity_id

SEED = Path("data/seed")
PRODUCT_MAP = [
    {"match": "Q663", "factor": "ALUMINIUM", "note": "aluminium"},
    {"match": "keyword:copper", "factor": "COPPER", "note": ""},
    {"match": "keyword:gold", "factor": "GOLD", "note": ""},
    {"match": "exclude:copper hydroxide", "factor": "-", "note": "fungicide"},
]


def read(name: str) -> list[dict]:
    """Rows of a seed file."""
    with (SEED / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# --- pure helpers ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("product_id", "label", "expected"),
    [
        ("Q663", "anything", "ALUMINIUM"),  # exact Q-id wins
        ("Q1", "refined copper cathodes", "COPPER"),
        ("Q2", "Gold bullion", "GOLD"),
        ("Q3", "golden retriever food", None),  # whole words only
        ("Q4", "clothing", None),
        ("Q5", "Copper hydroxide", None),  # explicit exclusion beats the keyword
        (None, None, None),
    ],
)
def test_factor_for_product(product_id, label, expected):
    """Q-id first, then whole-word keyword, else nothing."""
    assert declared.factor_for_product(product_id, label, PRODUCT_MAP) == expected


def test_company_links_dedupes_and_counts_unmatched():
    """Two copper products give one link; unmatched products are counted, never guessed."""
    rows = [
        {"isin": "ZZTEST000011", "company": "Q1", "product": "Q10", "label": "copper"},
        {"isin": "ZZTEST000011", "company": "Q1", "product": "Q11", "label": "copper wire"},
        {"isin": "ZZTEST000011", "company": "Q1", "product": "Q12", "label": "clothing"},
        {"isin": "ZZTEST000029", "company": "Q2", "product": None, "label": None},
    ]
    entities, links, unmatched = declared.company_links(rows, PRODUCT_MAP)
    assert {row["isin"] for row in entities} == {"ZZTEST000011", "ZZTEST000029"}
    assert links == [{"isin": "ZZTEST000011", "factor": "COPPER", "product": "copper", "product_id": "Q10"}]
    assert unmatched == {"clothing": 1}


def test_wikidata_helpers():
    """Entity ids are parsed; only ISIN-shaped values reach the SPARQL text."""
    assert entity_id("http://www.wikidata.org/entity/Q154037") == "Q154037" and entity_id(None) is None
    assert '"FR0000120271"' in build_query(["FR0000120271"])
    with pytest.raises(ValueError):
        build_query(['FR0000120271" } DELETE {'])


def test_seed_files_are_consistent():
    """Every factor named in the maps exists; currencies and rates are never declared; relations are valid."""
    factors = {row["code"]: row["kind"] for row in read("factors.csv")}
    industry = read("industry_factors.csv")
    products = read("product_factors.csv")
    assert {row["relation"] for row in industry} <= set(declared.RELATIONS)
    assert all(row["rationale"] for row in industry)
    for row in industry + [row for row in products if not row["match"].startswith("exclude:")]:
        assert row["factor"] in factors, row
        assert factors[row["factor"]] == "commodity", row
    assert set(factors.values()) == set(declared.SUB_LABELS)


# --- load into the test database ------------------------------------------------------------------

FACTORS = [
    {"code": "TEST-a-OIL", "name": "Test oil", "kind": "commodity", "unit": "x", "source": "test", "source_id": "x"},
    {"code": "TEST-a-FX", "name": "Test FX", "kind": "currency", "unit": "x", "source": "test", "source_id": "x"},
]
INDUSTRY = [{"industry_code": "TEST_A2_AIRLINES", "relation": "CONSUMES", "factor": "TEST-a-OIL", "rationale": "fuel"}]
CLEANUP = """
MATCH (n) WHERE n.code IN ['TEST-a-OIL', 'TEST-a-FX', 'TEST_A2_AIRLINES'] OR n.isin = 'ZZTEST000011'
DETACH DELETE n
"""


@pytest.fixture
def loaded(neo4j_client, monkeypatch):
    """A test industry and company, loaded twice, cleaned up afterwards."""
    neo4j_client.write(CLEANUP)
    neo4j_client.write("CREATE (:Industry {code: 'TEST_A2_AIRLINES', name: 'Test airlines'})")
    neo4j_client.write("CREATE (:Company {isin: 'ZZTEST000011', name: 'Test producer'})")
    monkeypatch.setattr(declared, "read_seed", lambda name: [{"match": "keyword:oil", "factor": "TEST-a-OIL"}])
    wikidata_rows = [{"isin": "ZZTEST000011", "company": "Q999", "product": "Q5", "label": "crude oil"}]
    declared.load(neo4j_client, FACTORS, INDUSTRY, wikidata_rows)
    report = declared.load(neo4j_client, FACTORS, INDUSTRY, wikidata_rows)
    yield neo4j_client, report
    neo4j_client.write(CLEANUP)


@pytest.mark.neo4j
def test_load_declared(loaded):
    """Sub-labels, industry and company links, provenance, idempotency."""
    client, report = loaded
    assert report["company_produces"] == 1 and report["companies_on_wikidata"] == 1
    labels = {
        row["code"]: row["labels"]
        for row in client.read(
            "MATCH (x:Factor) WHERE x.code STARTS WITH 'TEST-a-' RETURN x.code AS code, labels(x) AS labels"
        )
    }
    assert set(labels["TEST-a-OIL"]) == {"Factor", "Commodity"} and set(labels["TEST-a-FX"]) == {"Factor", "Currency"}
    rows = client.read("""
        MATCH (n:Industry {code: 'TEST_A2_AIRLINES'})-[i:CONSUMES]->(x:Factor {code: 'TEST-a-OIL'})
        MATCH (c:Company {isin: 'ZZTEST000011'})-[p:PRODUCES]->(x)
        RETURN count(i) AS industry, count(p) AS company, i.rationale AS why, p.source AS source,
               c.wikidata_id AS qid
        """)
    assert rows == [{"industry": 1, "company": 1, "why": "fuel", "source": "wikidata", "qid": "Q999"}]


@pytest.mark.neo4j
def test_mini_graph_links_are_never_removed(loaded):
    """Links without a `run` (the fixture's) survive the stale-link cleanup."""
    client, _ = loaded
    count = client.read("MATCH (:Industry)-[r:PRODUCES|CONSUMES]->(:Factor) WHERE r.run IS NULL RETURN count(r) AS n")
    assert count[0]["n"] == 5
