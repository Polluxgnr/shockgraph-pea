"""Models for shock simulation and path explanation."""

from pydantic import BaseModel, Field

from app.models.common import DISCLAIMER, EVIDENCE_PATTERN, TruthLevel
from app.models.portfolio import Position


class Shock(BaseModel):
    """A move of one factor, in percent (e.g. BRENT +20)."""

    factor_code: str = Field(min_length=2, max_length=32, examples=["BRENT"])
    pct: float = Field(ge=-90, le=300, examples=[20.0])


class ShockRequest(BaseModel):
    """One or several simultaneous shocks, optionally applied to a portfolio."""

    shocks: list[Shock] = Field(min_length=1, max_length=5)
    positions: list[Position] | None = Field(default=None, max_length=50)
    depth: int = Field(default=2, ge=1, le=3, description="1 = direct exposure only, 2-3 = through Claim links")
    min_r2: float = Field(default=0.1, ge=0, le=1)


class ImpactLine(BaseModel):
    """Estimated impact on one company, with the path that explains it."""

    isin: str
    name: str
    truth: TruthLevel
    impact_pct: float | None = Field(default=None, description="None when only a direction is known")
    impact_eur: float | None = None
    path: list[str] = Field(default_factory=list, description="Evidence refs, e.g. Factor:BRENT, Company:...")
    explanation: str = ""


class ShockResult(BaseModel):
    """Result of a shock simulation."""

    direct: list[ImpactLine]
    indirect: list[ImpactLine] = Field(default_factory=list)
    portfolio_impact_eur: float | None = None
    portfolio_impact_pct: float | None = None
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class PathResult(BaseModel):
    """The shortest explainable path between two nodes."""

    from_ref: str = Field(pattern=EVIDENCE_PATTERN)
    to_ref: str = Field(pattern=EVIDENCE_PATTERN)
    hops: int
    nodes: list[str]
    relations: list[str]
    truth_levels: list[TruthLevel]
