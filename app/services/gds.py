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
