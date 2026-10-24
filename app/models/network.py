"""Models for Graph Data Science results (role C)."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.companies import CompanySummary


class Cluster(BaseModel):
    """A community of stocks that move together (Louvain on CORRELATED_WITH)."""

    community_id: int
    size: int
    members: list[CompanySummary]
    top_factors: list[str] = Field(default_factory=list)


class RankedCompany(BaseModel):
    """A company with an algorithm score (e.g. PageRank)."""

    isin: str
    name: str
    score: float


class SimilarCompany(BaseModel):
    """A company similar to another one, with a similarity score in [0, 1]."""

    isin: str
    name: str
    score: float = Field(ge=0, le=1)


class GdsReport(BaseModel):
    """What a GDS refresh computed."""

    graphs: list[str]
    algorithms: dict[str, dict[str, Any]]
    duration_ms: int
