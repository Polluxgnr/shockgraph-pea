"""Models shared by every router."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

TruthLevel = Literal["fact", "estimate", "prediction", "hypothesis"]
FactorKind = Literal["commodity", "currency", "rate"]

ISIN_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"
EVIDENCE_PATTERN = r"^(Company|Fund|Index|Sector|Industry|Country|Factor|Article|Claim):.+$"

ItemT = TypeVar("ItemT")

DISCLAIMER = (
    "Educational and analytical tool only. Not investment advice: nothing here is a recommendation "
    "to buy or sell. Statistical estimates describe the past and do not prove causation."
)


class Page(BaseModel, Generic[ItemT]):
    """A page of results with an opaque cursor for the next page (None on the last page)."""

    items: list[ItemT]
    next_cursor: str | None = None


class Evidence(BaseModel):
    """A pointer to a node of the graph that supports a statement."""

    ref: str = Field(pattern=EVIDENCE_PATTERN, examples=["Company:FR0000120271"])
    truth: TruthLevel


class ErrorResponse(BaseModel):
    """Body of every error response."""

    detail: str


class HealthResponse(BaseModel):
    """Body of GET /api/health."""

    status: Literal["ok", "degraded"]
    neo4j: bool
    gds: bool | None = None
    version: str
