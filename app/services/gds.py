"""Graph Data Science service (role C). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.companies import Exposure
from app.models.network import Cluster, GdsReport, RankedCompany, SimilarCompany


class GdsService:
    """In-memory projections, algorithms and ML predictions."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def refresh(self) -> GdsReport:
        """Project, run the algorithms, write the results back, drop the projections.

        Louvain on CORRELATED_WITH (writes `community_id`), PageRank (writes `pagerank`),
        FastRP (writes `embedding`). Returns what was computed.
        """
        raise NotImplementedError("C2: GdsService.refresh")

    def clusters(self, limit: int = 10) -> list[Cluster]:
        """Communities of stocks that move together, largest first, with their dominant factors."""
        raise NotImplementedError("C2: GdsService.clusters")

    def systemic(self, limit: int = 20) -> list[RankedCompany]:
        """Most systemic companies by PageRank."""
        raise NotImplementedError("C2: GdsService.systemic")

    def similar(self, isin: str, k: int = 5) -> list[SimilarCompany] | None:
        """k most similar companies (cosine on FastRP embeddings), None if the company is unknown."""
        raise NotImplementedError("C2: GdsService.similar")

    def predicted_exposures(self, isin: str, limit: int = 10) -> list[Exposure] | None:
        """PREDICTED_SENSITIVITY links of a company (truth `prediction`), None if unknown."""
        raise NotImplementedError("C3: GdsService.predicted_exposures")
