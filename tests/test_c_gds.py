"""C2: Louvain, PageRank and FastRP on the mini graph (expected answers in tests/fixtures/README.md)."""

import threading

import pytest

from app.services import gds as gds_module
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


def communities(client) -> dict[str, int]:
    """community_id of every company that has one."""
    rows = client.read("MATCH (c:Company) WHERE c.community_id IS NOT NULL RETURN c.isin AS isin, c.community_id AS id")
    return {row["isin"]: row["id"] for row in rows}


def gds_state(client) -> dict:
    """What a refresh leaves behind: results, staged values and projections."""
    row = client.read(
        "MATCH (c:Company) RETURN count(c.community_id) AS communities, count(c.pagerank) AS ranked, "
        "count(c.embedding) AS embedded, count(c.community_id_next) + count(c.pagerank_next) "
        "+ count(c.embedding_next) AS staged"
    )[0]
    graphs = client.read("CALL gds.graph.list() YIELD graphName RETURN collect(graphName) AS names")[0]["names"]
    return {**row, "projections": [name for name in graphs if name.startswith(GRAPH_NAME)]}


class FailOn:
    """A real client that fails on the first query containing `marker` (an algorithm crashing mid-refresh)."""

    def __init__(self, client, marker: str) -> None:
        self.client = client
        self.marker = marker
        self.read = client.read

    def write(self, query, params=None):
        """Fail on the marked query, run the others."""
        if self.marker in query:
            raise RuntimeError(f"{self.marker} failed")
        return self.client.write(query, params)


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
    [graph] = report.graphs
    assert graph.startswith(GRAPH_NAME + "_")
    assert report.algorithms["projection"] == {"nodeCount": 9, "relationshipCount": 12}  # 6 links, both directions
    assert report.algorithms["louvain"]["communityCount"] == 3
    assert report.algorithms["pagerank"]["didConverge"] is True
    assert report.algorithms["fastrp"]["nodePropertiesWritten"] == 9
    assert report.algorithms["swap"]["swapped"] == 9
    assert gds_state(client) == {"communities": 9, "ranked": 9, "embedded": 9, "staged": 0, "projections": []}


@pytest.mark.neo4j
@pytest.mark.gds
def test_a_failure_after_louvain_keeps_the_previous_results(refreshed):
    """Review of #29: PageRank crashes after Louvain. The previous communities and embeddings are untouched,
    no staged value and no projection is left behind."""
    client, _ = refreshed
    before = communities(client)
    with pytest.raises(RuntimeError, match="pageRank"):
        GdsService(FailOn(client, "gds.pageRank")).refresh()
    assert communities(client) == before
    assert gds_state(client) == {"communities": 9, "ranked": 9, "embedded": 9, "staged": 0, "projections": []}


@pytest.mark.neo4j
@pytest.mark.gds
def test_two_refreshes_at_the_same_time_both_succeed(refreshed):
    """Review of #29: two POST /api/network/refresh at once. Each has its own projection, the lock runs them
    one after the other, and the graph ends complete with nothing left in memory."""
    client, _ = refreshed
    reports, errors = [], []

    def run():
        try:
            reports.append(GdsService(client).refresh())
        except Exception as error:  # pylint: disable=broad-exception-caught
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == [] and len(reports) == 2
    assert reports[0].graphs != reports[1].graphs
    assert gds_state(client) == {"communities": 9, "ranked": 9, "embedded": 9, "staged": 0, "projections": []}


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
    assert response.status_code == 200 and response.json()["graphs"][0].startswith(GRAPH_NAME + "_")


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
    assert "REMOVE c.community_id_next" in client.queries[-2][0]  # staged values discarded first
    assert not any("SET c.community_id" in query for query, _ in client.queries)  # never swapped in


def test_each_refresh_has_its_own_projection_and_holds_the_lock():
    """Two refreshes project two different graphs; the lock is held while a refresh talks to the database."""

    class LockWatcher(FakeClient):
        """Answers every query and notes whether the refresh lock was held."""

        def __init__(self) -> None:
            super().__init__(rows=[{"relationships": 6, "swapped": 9, "nodeCount": 9}])
            self.locked: list[bool] = []

        def write(self, query, params=None):
            """Record the query and the lock state."""
            self.locked.append(gds_module._REFRESH_LOCK.locked())  # pylint: disable=protected-access
            return super().write(query, params)

    client = LockWatcher()
    first, second = GdsService(client).refresh(), GdsService(client).refresh()
    assert first.graphs != second.graphs
    assert all(client.locked)
    dropped = [params["graph"] for query, params in client.queries if "gds.graph.drop" in query]
    assert dropped == first.graphs + second.graphs
