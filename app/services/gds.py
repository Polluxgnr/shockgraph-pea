"""Graph Data Science service (role C). Signatures are frozen: add methods, do not change these.

Each refresh projects its own in-memory graph, `market_<id>`: the companies that have at least one
CORRELATED_WITH, with those links undirected and weighted by `rho`. Louvain, PageRank and FastRP run on it in
`mutate` mode (results stay in memory), are written to the nodes under staging names (`community_id_next`...),
and one Cypher transaction swaps them into `community_id`, `pagerank` and `embedding`. If anything fails before
the swap, the staging properties are removed and the previous results stay untouched: the graph never holds a
mix of two refreshes. Reads never touch GDS: they query the swapped properties.

The staging properties are shared by every process, so a refresh first takes a lock stored in Neo4j (a
`GdsRefreshLock` node holding its token): a second API worker or `make gds` meanwhile gets `RefreshInProgress`
instead of mixing its values in. The swap and the discard only run in a transaction that still matches that
token, so a refresh that lost its lock can neither publish nor erase anything.
"""

import threading
import time
import uuid

from app.config import get_settings
from app.database.neo4j import Neo4jClient
from app.models.companies import Exposure
from app.models.network import Cluster, GdsReport, RankedCompany, SimilarCompany

LOCK_NAME = "refresh"
LOCK_STALE_SECONDS = 1_800  # a lock older than this was left by a crashed process: the next refresh takes it
GRAPH_NAME = "market"  # prefix: each refresh projects `market_<id>`, so two refreshes never drop each other's
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

# concurrency 1: Louvain processes nodes in a fixed order, so the same graph gives the same communities.
LOUVAIN = """
CALL gds.louvain.mutate($graph, {relationshipWeightProperty: 'rho', mutateProperty: 'community_id_next',
                                 concurrency: 1})
YIELD communityCount, modularity, ranLevels, nodePropertiesWritten
RETURN communityCount, modularity, ranLevels, nodePropertiesWritten
"""

# 20 iterations (the default) is not enough on small chains and stars: the error shrinks by about the damping
# factor (0.85) per iteration. 100 iterations stay cheap and let the report say didConverge = true.
PAGERANK = """
CALL gds.pageRank.mutate($graph, {relationshipWeightProperty: 'rho', mutateProperty: 'pagerank_next',
                                  maxIterations: 100})
YIELD ranIterations, didConverge, nodePropertiesWritten
RETURN ranIterations, didConverge, nodePropertiesWritten
"""

FASTRP = """
CALL gds.fastRP.mutate($graph, {embeddingDimension: $dimension, relationshipWeightProperty: 'rho',
                                randomSeed: $seed, concurrency: 1, mutateProperty: 'embedding_next'})
YIELD nodeCount, nodePropertiesWritten
RETURN nodeCount, nodePropertiesWritten
"""

# The three results go to the database together, under staging names.
WRITE_STAGED = """
CALL gds.graph.nodeProperties.write($graph, ['community_id_next', 'pagerank_next', 'embedding_next'])
YIELD propertiesWritten
RETURN propertiesWritten
"""

# The constraint makes MERGE on the lock safe between processes: two of them can never create two lock nodes.
LOCK_CONSTRAINT = (
    "CREATE CONSTRAINT gds_refresh_lock IF NOT EXISTS FOR (lock:GdsRefreshLock) REQUIRE lock.name IS UNIQUE"
)

# Creating the node takes the lock. An existing one is first written to (`checked_at`), which makes this
# transaction wait for Neo4j's lock on the node, then read again: only an abandoned lock is taken over.
ACQUIRE = """
MERGE (lock:GdsRefreshLock {name: $lock})
ON CREATE SET lock.token = $token, lock.acquired_at = datetime()
ON MATCH SET lock.checked_at = datetime()
WITH lock
CALL (lock) {
  WITH lock
  WHERE lock.token <> $token AND lock.acquired_at < datetime() - duration({seconds: $stale})
  SET lock.token = $token, lock.acquired_at = datetime()
}
RETURN lock.token = $token AS acquired, toString(lock.acquired_at) AS since
"""

RELEASE = "MATCH (lock:GdsRefreshLock {name: $lock, token: $token}) DELETE lock RETURN count(*) AS released"

# One transaction, only while this refresh still holds the lock: every company takes its staged values, or
# loses its old ones when it has none (a company that lost all its correlations must not keep an old community
# or embedding). All or nothing. No row back means the lock was lost and nothing changed.
SWAP = """
MATCH (lock:GdsRefreshLock {name: $lock, token: $token})
SET lock.swapped_at = datetime()
WITH lock
CALL (lock) {
  MATCH (c:Company)
  WHERE c.community_id IS NOT NULL OR c.pagerank IS NOT NULL OR c.embedding IS NOT NULL
     OR c.community_id_next IS NOT NULL
  SET c.community_id = c.community_id_next, c.pagerank = c.pagerank_next, c.embedding = c.embedding_next
  REMOVE c.community_id_next, c.pagerank_next, c.embedding_next
  RETURN count(c) AS swapped
}
RETURN swapped
"""

# Before staging (leftovers of a crashed refresh) and after a failure: staged values are thrown away, the
# previous results stay. Also only while holding the lock, so it never erases another refresh's staging.
DISCARD = """
MATCH (lock:GdsRefreshLock {name: $lock, token: $token})
CALL (lock) {
  MATCH (c:Company)
  WHERE c.community_id_next IS NOT NULL OR c.pagerank_next IS NOT NULL OR c.embedding_next IS NOT NULL
  REMOVE c.community_id_next, c.pagerank_next, c.embedding_next
  RETURN count(c) AS discarded
}
RETURN discarded
"""

# Two refreshes in this process run one after the other (the Neo4j lock would refuse the second one).
_REFRESH_LOCK = threading.Lock()


class RefreshInProgress(RuntimeError):
    """Another process holds the refresh lock, or this refresh lost it before the swap."""


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
        run_id = uuid.uuid4().hex[:12]
        lock = {"lock": LOCK_NAME, "token": run_id}
        with _REFRESH_LOCK:
            self.client.write(LOCK_CONSTRAINT)
            held = self.client.write(ACQUIRE, {**lock, "stale": LOCK_STALE_SECONDS})
            if not held or not held[0]["acquired"]:
                since = held[0]["since"] if held else "?"
                raise RefreshInProgress(f"another GDS refresh holds the lock since {since}")
            try:
                return self._refresh(f"{GRAPH_NAME}_{run_id}", lock)
            finally:
                self.client.write(RELEASE, lock)

    def _swap(self, lock: dict) -> dict:
        """Publish the staged values, or fail without changing anything if the lock was lost."""
        swapped = self.client.write(SWAP, lock)
        if not swapped:
            raise RefreshInProgress("the GDS refresh lock was taken over before the swap: nothing was published")
        return swapped[0]

    def _refresh(self, graph: str, lock: dict) -> GdsReport:
        """One refresh on its own projection, under the lock: stage everything, then swap in one transaction."""
        start = time.perf_counter()
        params = {"graph": graph}
        algorithms: dict[str, dict] = {}
        self.client.write(DISCARD, lock)  # staging left by a refresh that crashed
        edges = self.client.read(COUNT_EDGES)
        if not edges or not edges[0]["relationships"]:
            algorithms["swap"] = self._swap(lock)  # nothing staged: old results are cleared
            return GdsReport(graphs=[], algorithms=algorithms, duration_ms=_elapsed_ms(start))
        swapped_in = False
        try:
            algorithms["projection"] = self.client.write(PROJECT, params)[0]
            algorithms["louvain"] = self.client.write(LOUVAIN, params)[0]
            algorithms["pagerank"] = self.client.write(PAGERANK, params)[0]
            fastrp_params = {**params, "dimension": EMBEDDING_DIMENSION, "seed": RANDOM_SEED}
            algorithms["fastrp"] = self.client.write(FASTRP, fastrp_params)[0]
            algorithms["staged"] = self.client.write(WRITE_STAGED, params)[0]
            algorithms["swap"] = self._swap(lock)
            swapped_in = True
        finally:
            if not swapped_in:
                self.client.write(DISCARD, lock)
            self.client.write(DROP, params)
        return GdsReport(graphs=[graph], algorithms=algorithms, duration_ms=_elapsed_ms(start))

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
        """PREDICTED_SENSITIVITY links of a company (truth `prediction`), None if unknown.

        Highest probability first. `source` names the model version that made the prediction.
        """
        # A known company without predictions gives one row of nulls (OPTIONAL MATCH), an unknown one no row.
        query = """
        MATCH (c:Company {isin: $isin})
        OPTIONAL MATCH (c)-[p:PREDICTED_SENSITIVITY]->(x:Factor)
        RETURN c.name AS company_name, x.code AS factor_code, x.name AS factor_name, p.prob AS prob,
               p.model_version AS model_version
        ORDER BY prob DESC, factor_code
        LIMIT $limit
        """
        rows = self.client.read(query, {"isin": isin, "limit": limit})
        if not rows:
            return None
        return [
            Exposure(
                company_isin=isin,
                company_name=row["company_name"],
                factor_code=row["factor_code"],
                factor_name=row["factor_name"],
                relation="PREDICTED_SENSITIVITY",
                truth="prediction",
                prob=row["prob"],
                source=row["model_version"],
            )
            for row in rows
            if row["factor_code"] is not None
        ]


def _elapsed_ms(start: float) -> int:
    """Milliseconds since `start` (time.perf_counter)."""
    return int((time.perf_counter() - start) * 1000)
