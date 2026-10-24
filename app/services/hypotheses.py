"""LLM-extracted hypotheses (role A). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.hypotheses import Hypothesis


class HypothesisService:
    """Reads Claim nodes with their provenance."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def list_claims(
        self, company_isin: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[Hypothesis]:
        """Claims (as subject or object of `company_isin` when given), most confident first."""
        raise NotImplementedError("A3: HypothesisService.list_claims")
