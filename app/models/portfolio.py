"""Models for the portfolio look-through (x-ray)."""

from pydantic import BaseModel, Field

from app.models.common import DISCLAIMER, ISIN_PATTERN


class Position(BaseModel):
    """One line of a PEA: a stock or a fund, by ISIN, with the amount invested."""

    isin: str = Field(pattern=ISIN_PATTERN, examples=["FR0011869353"])
    amount_eur: float = Field(gt=0, le=10_000_000, examples=[1000.0])


class PortfolioRequest(BaseModel):
    """A portfolio. It is never stored: it only lives for the duration of the request."""

    positions: list[Position] = Field(min_length=1, max_length=50)
    look_through: bool = Field(default=True, description="Follow Fund-TRACKS->Index-CONSTITUENT->Company")


class ExposureLine(BaseModel):
    """Money exposed to one company, sector or country."""

    key: str
    label: str
    amount_eur: float
    weight: float = Field(ge=0, le=1)


class FactorExposureLine(BaseModel):
    """Portfolio-level sensitivity to a factor (weighted beta over the covered part)."""

    factor_code: str
    weighted_beta: float
    covered_weight: float = Field(ge=0, le=1, description="Share of the portfolio with an estimate")


class XRayResult(BaseModel):
    """What the portfolio is really exposed to."""

    total_eur: float
    companies: list[ExposureLine]
    sectors: list[ExposureLine]
    countries: list[ExposureLine]
    factors: list[FactorExposureLine]
    unknown_isins: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER
