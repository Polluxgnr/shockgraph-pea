"""Hypotheses endpoint (role A): relations suggested by news, with provenance."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.database.neo4j import Neo4jClient, get_client
from app.models.common import ISIN_PATTERN
from app.models.hypotheses import Hypothesis
from app.services.hypotheses import HypothesisService

router = APIRouter(prefix="/api", tags=["hypotheses"])


def get_hypothesis_service(client: Neo4jClient = Depends(get_client)) -> HypothesisService:
    """Build the service for one request."""
    return HypothesisService(client)


@router.get("/hypotheses", response_model=list[Hypothesis])
def list_hypotheses(
    company: str | None = Query(default=None, pattern=ISIN_PATTERN),
    status: Literal["hypothesis", "confirmed", "rejected"] | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    service: HypothesisService = Depends(get_hypothesis_service),
) -> list[Hypothesis]:
    """LLM-extracted relations between companies. These are hypotheses, not facts."""
    return service.list_claims(company_isin=company, status=status, limit=limit)
