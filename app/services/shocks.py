"""Shock propagation and path explanation (role B). Signatures are frozen."""

from app.database.neo4j import Neo4jClient
from app.models.shocks import PathResult, ShockRequest, ShockResult

MAX_PATH_HOPS = 6


class ShockService:
    """Simulates factor shocks on companies and portfolios."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def simulate(self, request: ShockRequest) -> ShockResult:
        """Propagate the shocks.

        Direct: `impact_pct = beta * pct` for each SENSITIVE_TO with r2 >= min_r2 (truth `estimate`);
        PRODUCES/CONSUMES add a direction without a number (truth `fact`).
        Indirect (depth >= 2): companies linked to a hit company through a Claim (SUPPLIES), truth
        `hypothesis`, no number. With positions, the impact is aggregated in euros (look-through).
        Unknown factor codes raise KeyError (the router answers 404).
        """
        raise NotImplementedError("B3: ShockService.simulate")

    def path(self, from_ref: str, to_ref: str, max_hops: int = 4) -> PathResult | None:
        """Shortest explainable path between two refs (`Label:key`), or None if there is none.

        `max_hops` cannot be a Cypher parameter in a variable-length pattern: validate it as an int in
        [1, MAX_PATH_HOPS] in Python before inserting it in the query.
        """
        raise NotImplementedError("B3: ShockService.path")
