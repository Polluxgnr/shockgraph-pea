"""Graph Data Science service (role C). Signatures are frozen: add methods, do not change these.

One in-memory projection, `market`: the companies that have at least one CORRELATED_WITH, with those links
undirected and weighted by `rho`. Louvain, PageRank and FastRP run on it, write `community_id`, `pagerank`
and `embedding` back onto the Company nodes, and the projection is dropped. Reads never touch GDS: they
query the written properties, so the API keeps working while no projection exists.
"""

import time

from app.config import get_settings
from app.database.neo4j import Neo4jClient
from app.models.companies import Exposure
from app.models.network import Cluster, GdsReport, RankedCompany, SimilarCompany

GRAPH_NAME = "market"
EMBEDDING_DIMENSION = 64
RANDOM_SEED = 42
MEMBERS_PER_CLUSTER = 20
FACTORS_PER_CLUSTER = 3

COUNT_EDGES = "MATCH (:Company)-[r:CORRELATED_WITH]->(:Company) RETURN count(r) AS relationships"

DROP = "CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName"

# Cypher projection: only companies with a correlation become nodes (an isolated stock would get its own
# community, a base PageRank and a zero embedding, which says nothing). Stored once, projected undirected.
PROJECT = """
MATCH (source:Company)-[r:CORRELATED_WITH]->(target:Company)
WITH gds.graph.project($graph, source, target, {relationshipProperties: {rho: r.rho}},
                       {undirectedRelationshipTypes: ['*']}) AS graph
RETURN graph.nodeCount AS nodeCount, graph.relationshipCount AS relationshipCount
"""

# Results of a previous refresh are removed first: a company that lost all its correlations must not keep
# an old community or embedding.
CLEAR = """
MATCH (c:Company) WHERE c.community_id IS NOT NULL OR c.pagerank IS NOT NULL OR c.embedding IS NOT NULL
REMOVE c.community_id, c.pagerank, c.embedding
RETURN count(c) AS cleared
"""

# concurrency 1: Louvain processes nodes in a fixed order, so the same graph gives the same communities.
LOUVAIN = """
CALL gds.louvain.write($graph, {relationshipWeightProperty: 'rho', writeProperty: 'community_id', concurrency: 1})
YIELD communityCount, modularity, ranLevels, nodePropertiesWritten
RETURN communityCount, modularity, ranLevels, nodePropertiesWritten
"""

# 20 iterations (the default) is not enough on small chains and stars: the error shrinks by about the damping
# factor (0.85) per iteration. 100 iterations stay cheap and let the report say didConverge = true.
PAGERANK = """
CALL gds.pageRank.write($graph, {relationshipWeightProperty: 'rho', writeProperty: 'pagerank', maxIterations: 100})
YIELD ranIterations, didConverge, nodePropertiesWritten
RETURN ranIterations, didConverge, nodePropertiesWritten
"""

FASTRP = """
CALL gds.fastRP.write($graph, {embeddingDimension: $dimension, relationshipWeightProperty: 'rho',
                               randomSeed: $seed, concurrency: 1, writeProperty: 'embedding'})
YIELD nodeCount, nodePropertiesWritten
RETURN nodeCount, nodePropertiesWritten
"""

# Largest communities first. Members are capped (a real community can hold hundreds of stocks) and ordered
# by PageRank, so the most central ones are shown. Dominant factors: the factors to which the most members
# have a significant estimate (r2 >= min_r2).
CLUSTERS = """
MATCH (c:Company) WHERE c.community_id IS NOT NULL
WITH c.community_id AS community_id, c ORDER BY c.pagerank DESC, c.isin
WITH community_id, collect(c) AS members
WITH community_id, members, size(members) AS member_count
ORDER BY member_count DESC, community_id
LIMIT $limit
CALL (members) {
  UNWIND members AS member
  MATCH (member)-[r:SENSITIVE_TO]->(x:Factor) WHERE r.r2 >= $min_r2
  WITH x.code AS code, count(DISTINCT member) AS exposed
  ORDER BY exposed DESC, code
  LIMIT $factors
  RETURN collect(code) AS top_factors
}
UNWIND members[..$members] AS member
OPTIONAL MATCH (member)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(s:Sector)
OPTIONAL MATCH (member)-[:HEADQUARTERED_IN]->(k:Country)
WITH community_id, member_count, top_factors, member, s, k
RETURN community_id, member_count AS size, top_factors,
       collect({isin: member.isin, name: member.name, ticker: member.ticker, sector: s.name, country: k.code,
                pea_eligible: coalesce(member.pea_eligible, false)}) AS members
ORDER BY size DESC, community_id
"""

SYSTEMIC = """
MATCH (c:Company) WHERE c.pagerank IS NOT NULL
RETURN c.isin AS isin, c.name AS name, c.pagerank AS score
ORDER BY score DESC, isin
LIMIT $limit
"""

# Cosine on the written embeddings: one pass over the companies that have one (O(n x dimension) per call).
# Negative cosines mean "unrelated position", not similarity, so they are not returned. Scores are rounded
# (ties are broken by ISIN, so the answer is stable) and capped at 1 (float error can give 1.0000000002).
SIMILAR = """
MATCH (c:Company {isin: $isin})
OPTIONAL MATCH (other:Company)
WHERE other <> c AND c.embedding IS NOT NULL AND other.embedding IS NOT NULL
WITH c, other, CASE WHEN other IS NULL THEN null
                    ELSE round(gds.similarity.cosine(c.embedding, other.embedding), 6) END AS raw
WITH c, other, CASE WHEN raw > 1.0 THEN 1.0 ELSE raw END AS score
ORDER BY score DESC, other.isin
WITH c, collect(CASE WHEN score > 0 THEN {isin: other.isin, name: other.name, score: score} END)[..$k] AS similar
RETURN c.isin AS isin, similar
"""


class GdsService:
    """In-memory projections, algorithms and ML predictions."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def refresh(self) -> GdsReport:
        """Project, run the algorithms, write the results back, drop the projections.

        Louvain on CORRELATED_WITH (writes `community_id`), PageRank (writes `pagerank`),
        FastRP (writes `embedding`). Returns what was computed.
        """
        start = time.perf_counter()
        params = {"graph": GRAPH_NAME}
        cleared = self.client.write(CLEAR)
        algorithms: dict[str, dict] = {"clear": cleared[0] if cleared else {"cleared": 0}}
        edges = self.client.read(COUNT_EDGES)
        if not edges or not edges[0]["relationships"]:
            return GdsReport(graphs=[], algorithms=algorithms, duration_ms=_elapsed_ms(start))
        self.client.write(DROP, params)  # a projection left by an interrupted run
        try:
            algorithms["projection"] = self.client.write(PROJECT, params)[0]
            algorithms["louvain"] = self.client.write(LOUVAIN, params)[0]
            algorithms["pagerank"] = self.client.write(PAGERANK, params)[0]
            fastrp_params = {**params, "dimension": EMBEDDING_DIMENSION, "seed": RANDOM_SEED}
            algorithms["fastrp"] = self.client.write(FASTRP, fastrp_params)[0]
        finally:
            self.client.write(DROP, params)
        return GdsReport(graphs=[GRAPH_NAME], algorithms=algorithms, duration_ms=_elapsed_ms(start))

    def clusters(self, limit: int = 10) -> list[Cluster]:
        """Communities of stocks that move together, largest first, with their dominant factors."""
        params = {
            "limit": limit,
            "members": MEMBERS_PER_CLUSTER,
            "factors": FACTORS_PER_CLUSTER,
            "min_r2": get_settings().min_r2_default,
        }
        return [Cluster(**row) for row in self.client.read(CLUSTERS, params)]

    def systemic(self, limit: int = 20) -> list[RankedCompany]:
        """Most systemic companies by PageRank."""
        return [RankedCompany(**row) for row in self.client.read(SYSTEMIC, {"limit": limit})]

    def similar(self, isin: str, k: int = 5) -> list[SimilarCompany] | None:
        """k most similar companies (cosine on FastRP embeddings), None if the company is unknown."""
        rows = self.client.read(SIMILAR, {"isin": isin, "k": k})
        if not rows:
            return None
        return [SimilarCompany(**item) for item in rows[0]["similar"]]

    def predicted_exposures(self, isin: str, limit: int = 10) -> list[Exposure] | None:
        """PREDICTED_SENSITIVITY links of a company (truth `prediction`), None if unknown."""
        raise NotImplementedError("C3: GdsService.predicted_exposures")


def _elapsed_ms(start: float) -> int:
    """Milliseconds since `start` (time.perf_counter)."""
    return int((time.perf_counter() - start) * 1000)
