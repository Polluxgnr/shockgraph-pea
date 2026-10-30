"""Community and embedding of each company in one window's correlation network, with GDS (role C, C3).

C2 writes `community_id` and `embedding` for the current CORRELATED_WITH links only. The model also needs
them for the older window, whose correlations are not in the graph, so this module projects a list of pairs
(`UNWIND $pairs`) into a temporary in-memory graph, streams Louvain and FastRP with the same settings as
C2, and drops the projection. Nothing is written to the database.
"""

from app.database.neo4j import Neo4jClient
from ml.features import Positions

GRAPH_NAME = "ml_window"  # not C2's "market": both can exist at the same time
EMBEDDING_DIMENSION = 64
RANDOM_SEED = 42
MAX_ROWS = 20_000

DROP = "CALL gds.graph.drop($graph, false) YIELD graphName RETURN graphName"

PROJECT = """
UNWIND $pairs AS pair
MATCH (source:Company {isin: pair.a})
MATCH (target:Company {isin: pair.b})
WITH gds.graph.project($graph, source, target, {relationshipProperties: {rho: pair.rho}},
                       {undirectedRelationshipTypes: ['*']}) AS graph
RETURN graph.nodeCount AS nodeCount, graph.relationshipCount AS relationshipCount
"""

# concurrency 1 and a fixed seed, as in C2: the same window gives the same communities and embeddings.
LOUVAIN = """
CALL gds.louvain.stream($graph, {relationshipWeightProperty: 'rho', concurrency: 1})
YIELD nodeId, communityId
RETURN gds.util.asNode(nodeId).isin AS isin, communityId AS community
LIMIT $limit
"""

FASTRP = """
CALL gds.fastRP.stream($graph, {embeddingDimension: $dimension, relationshipWeightProperty: 'rho',
                                randomSeed: $seed, concurrency: 1})
YIELD nodeId, embedding
RETURN gds.util.asNode(nodeId).isin AS isin, embedding
LIMIT $limit
"""


def network_positions(client: Neo4jClient, pairs: list[dict]) -> Positions:
    """Louvain community and FastRP embedding of every company in `pairs` ({a, b, rho})."""
    if not pairs:
        return Positions({}, {})
    graph = {"graph": GRAPH_NAME}
    client.write(DROP, graph)  # a run that crashed may have left it behind
    try:
        client.write(PROJECT, {**graph, "pairs": pairs})
        communities = client.read(LOUVAIN, {**graph, "limit": MAX_ROWS})
        embeddings = client.read(
            FASTRP, {**graph, "dimension": EMBEDDING_DIMENSION, "seed": RANDOM_SEED, "limit": MAX_ROWS}
        )
    finally:
        client.write(DROP, graph)
    return Positions(
        {row["isin"]: row["community"] for row in communities},
        {row["isin"]: list(row["embedding"]) for row in embeddings},
    )
