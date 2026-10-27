"""C2: Louvain, PageRank and FastRP on the mini graph (expected answers in tests/fixtures/README.md)."""

import pytest

from app.services.gds import GRAPH_NAME, GdsService
from tests.conftest import FakeClient

PETROLIA, AEROLYS, VARENNE, SILIQA = "FRTEST000011", "FRTEST000029", "FRTEST000037", "NLTEST000045"
ORSAY, AUTOROUTE, CUPRUM, HELIOS, VELA = "FRTEST000052", "DETEST000060", "DETEST000078", "FRTEST000086", "USTEST000094"
COMMUNITIES = [{PETROLIA, CUPRUM, AUTOROUTE, ORSAY}, {AEROLYS, HELIOS}, {VARENNE, SILIQA, VELA}]

CLEANUP = "MATCH (c:Company) REMOVE c.community_id, c.pagerank, c.embedding"


@pytest.fixture(scope="module")
def refreshed(neo4j_client):
    """Run one refresh on the mini graph, then remove what it wrote so the mini graph stays intact."""
    if not neo4j_client.has_gds():
        pytest.skip("GDS plugin not installed in the test database")
    report = GdsService(neo4j_client).refresh()
    yield neo4j_client, report
    neo4j_client.write(CLEANUP)
    neo4j_client.write("CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName", {"graph": GRAPH_NAME})


def communities(client) -> dict[str, int]:
    """community_id of every company that has one."""
    rows = client.read("MATCH (c:Company) WHERE c.community_id IS NOT NULL RETURN c.isin AS isin, c.community_id AS id")
    return {row["isin"]: row["id"] for row in rows}


# --- refresh -------------------------------------------------------------------------------------


@pytest.mark.neo4j
@pytest.mark.gds
def test_louvain_finds_the_three_expected_communities(refreshed):
    """Pairs in the same expected community share an id; pairs across communities do not."""
    client, _ = refreshed
    found = communities(client)
    assert set(found) == set().union(*COMMUNITIES)
    for group in COMMUNITIES:
        assert len({found[isin] for isin in group}) == 1, group
    assert len({found[next(iter(group))] for group in COMMUNITIES}) == 3


@pytest.mark.neo4j
@pytest.mark.gds
def test_every_company_gets_a_pagerank_and_an_embedding(refreshed):
    """All 9 fixture companies are correlated with someone, so all are projected."""
    client, _ = refreshed
    row = client.read(
        "MATCH (c:Company) WHERE c.isin IN $isins "
        "RETURN count(c.pagerank) AS ranked, count(c.embedding) AS embedded, min(size(c.embedding)) AS dimension",
        {"isins": list(set().union(*COMMUNITIES))},
    )[0]
    assert row == {"ranked": 9, "embedded": 9, "dimension": 64}


@pytest.mark.neo4j
@pytest.mark.gds
def test_report_and_projection_dropped(refreshed):
    """The report says what ran; no projection is left in memory."""
    client, report = refreshed
    assert report.graphs == [GRAPH_NAME]
    assert report.algorithms["projection"] == {"nodeCount": 9, "relationshipCount": 12}  # 6 links, both directions
    assert report.algorithms["louvain"]["communityCount"] == 3
    assert report.algorithms["pagerank"]["didConverge"] is True
    assert report.algorithms["fastrp"]["nodePropertiesWritten"] == 9
    assert client.read("RETURN gds.graph.exists($graph) AS exists", {"graph": GRAPH_NAME}) == [{"exists": False}]


# --- reads ---------------------------------------------------------------------------------------


@pytest.mark.neo4j
@pytest.mark.gds
def test_clusters_largest_first_with_dominant_factors(refreshed):
    """Sizes 4, 3, 2; factors only from estimates with r2 >= 0.1 (Helios's 0.05 does not count)."""
    client, _ = refreshed
    clusters = GdsService(client).clusters()
    assert [cluster.size for cluster in clusters] == [4, 3, 2]
    assert {member.isin for member in clusters[0].members} == COMMUNITIES[0]
    assert clusters[0].top_factors == ["BRENT", "COPPER", "EUR10Y"]
    assert clusters[2].top_factors == ["BRENT"]
    assert clusters[1].top_factors == ["EURUSD"]  # Varenne 0.30 and Siliqa 0.12; Siliqa's COPPER r2 is 0.02
    petrolia = next(member for member in clusters[0].members if member.isin == PETROLIA)
    assert (petrolia.sector, petrolia.country, petrolia.pea_eligible) == ("Energy", "FR", True)
    assert len(GdsService(client).clusters(limit=1)) == 1


@pytest.mark.neo4j
@pytest.mark.gds
def test_systemic_ranks_hubs_above_leaves(refreshed):
    """Siliqa links Varenne and Vela, so it ranks above both; scores are sorted."""
    client, _ = refreshed
    ranked = GdsService(client).systemic(limit=9)
    scores = {company.isin: company.score for company in ranked}
    assert len(ranked) == 9 and [company.score for company in ranked] == sorted(scores.values(), reverse=True)
    assert scores[SILIQA] > scores[VARENNE] and scores[SILIQA] > scores[VELA]
    assert len(GdsService(client).systemic(limit=2)) == 2


@pytest.mark.neo4j
@pytest.mark.gds
def test_similar_uses_the_embeddings(refreshed):
    """Varenne's closest companies are the two others of its star (Siliqa, Vela). Never itself.

    With FastRP's default iteration weights (0, 1, 1), the hub and the leaves of a 3-node star see the same
    nodes within two hops, so their embeddings are identical (cosine 1): the tie is broken by ISIN.
    """
    client, _ = refreshed
    similar = GdsService(client).similar(VARENNE, k=3)
    assert [company.isin for company in similar[:2]] == [SILIQA, VELA]
    assert similar[0].score == pytest.approx(1.0) and similar[1].score == pytest.approx(1.0)
    assert VARENNE not in {company.isin for company in similar}
    assert len(similar) <= 3 and all(0 < company.score <= 1 for company in similar)
    assert [company.score for company in similar] == sorted((company.score for company in similar), reverse=True)


@pytest.mark.neo4j
@pytest.mark.gds
def test_similar_unknown_company_is_none(refreshed):
    """Unknown ISIN: None (the router answers 404)."""
    client, _ = refreshed
    assert GdsService(client).similar("XX0000000000") is None


@pytest.mark.neo4j
@pytest.mark.gds
def test_network_endpoints(refreshed, api_with, auth_headers):
    """Clusters, systemic and similar answer 200 on the refreshed graph; unknown ISIN is 404."""
    client, _ = refreshed
    api = api_with(client)
    assert [item["size"] for item in api.get("/api/network/clusters").json()] == [4, 3, 2]
    assert api.get("/api/network/systemic", params={"limit": 3}).status_code == 200
    similar = api.get(f"/api/companies/{VARENNE}/similar", params={"k": 2}).json()
    assert [item["isin"] for item in similar] == [SILIQA, VELA]
    assert api.get("/api/companies/XX0000000000/similar").status_code == 404
    response = api.post("/api/network/refresh", headers=auth_headers)
    assert response.status_code == 200 and response.json()["graphs"] == [GRAPH_NAME]


# --- without a database --------------------------------------------------------------------------


def test_refresh_without_correlations_runs_nothing():
    """No CORRELATED_WITH yet (before C1): old results are cleared, no projection is created."""
    client = FakeClient(rows=[{"relationships": 0, "cleared": 0}])
    report = GdsService(client).refresh()
    assert report.graphs == [] and not any("gds.graph.project" in query for query, _ in client.queries)


def test_reads_on_an_empty_graph():
    """Before any refresh, reads answer empty lists rather than failing."""
    client = FakeClient(rows=[])
    service = GdsService(client)
    assert service.clusters() == [] and service.systemic() == []
    assert service.similar(VARENNE) is None


def test_projection_is_dropped_even_when_an_algorithm_fails():
    """A failing algorithm must not leave the projection in memory."""

    class FailingClient(FakeClient):
        """Fails on Louvain."""

        def write(self, query, params=None):
            """Record, then fail on Louvain."""
            self.queries.append((query, params or {}))
            if "gds.louvain" in query:
                raise RuntimeError("louvain failed")
            return [{"relationships": 6, "cleared": 0, "nodeCount": 9, "relationshipCount": 12}]

        read = write

    client = FailingClient()
    with pytest.raises(RuntimeError):
        GdsService(client).refresh()
    assert "gds.graph.drop" in client.queries[-1][0]
