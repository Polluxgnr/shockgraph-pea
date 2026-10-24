"""Models for companies and factors."""

from pydantic import BaseModel, Field

from app.models.common import ISIN_PATTERN, FactorKind, TruthLevel


class CompanySummary(BaseModel):
    """A company as it appears in lists."""

    isin: str = Field(pattern=ISIN_PATTERN, examples=["FR0000120271"])
    name: str = Field(examples=["TotalEnergies SE"])
    ticker: str | None = Field(default=None, examples=["TTE.PA"])
    sector: str | None = Field(default=None, examples=["Energy"])
    country: str | None = Field(default=None, examples=["FR"])
    pea_eligible: bool = True


class CompanyDetail(CompanySummary):
    """A company with its full profile."""

    industry: str | None = None
    lei: str | None = None
    market_cap_eur: float | None = None
    indexes: list[str] = Field(default_factory=list)


class FactorSummary(BaseModel):
    """A commodity, currency or rate that companies can be exposed to."""

    code: str = Field(examples=["BRENT"])
    name: str = Field(examples=["Brent crude oil"])
    kind: FactorKind
    unit: str | None = None


class Exposure(BaseModel):
    """One link between a company and a factor, with its level of truth."""

    company_isin: str
    company_name: str
    factor_code: str
    factor_name: str
    relation: str = Field(
        examples=["SENSITIVE_TO"], description="SENSITIVE_TO, PRODUCES, CONSUMES or PREDICTED_SENSITIVITY"
    )
    truth: TruthLevel
    beta: float | None = None
    r2: float | None = None
    prob: float | None = None
    source: str | None = None
