"""Company and factor queries (role B). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.common import Page
from app.models.companies import CompanyDetail, CompanySummary, Exposure, FactorSummary


class CompanyService:
    """Read-only queries about companies."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def search(
        self,
        query: str | None = None,
        sector: str | None = None,
        pea_only: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[CompanySummary]:
        """Search companies by name/ticker (full-text `company_name_ft`) with optional filters.

        Ordered by `market_cap_eur` descending then `isin`; cursor pagination (base64 of the last
        market cap and isin), never SKIP.
        """
        raise NotImplementedError("B1: CompanyService.search")

    def get(self, isin: str) -> CompanyDetail | None:
        """Return one company with sector, industry, country and indexes, or None if unknown."""
        raise NotImplementedError("B1: CompanyService.get")

    def exposures(self, isin: str, min_r2: float = 0.0) -> list[Exposure]:
        """All factor links of a company (SENSITIVE_TO, PRODUCES, CONSUMES), each with its truth level."""
        raise NotImplementedError("B1: CompanyService.exposures")

    def resolve(self, name: str, limit: int = 5) -> list[CompanySummary]:
        """Fuzzy name to companies (full-text index). Used by the agent to resolve entities."""
        raise NotImplementedError("B1: CompanyService.resolve")


class FactorService:
    """Read-only queries about commodities, currencies and rates."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def list_factors(self) -> list[FactorSummary]:
        """Every factor, ordered by kind then code."""
        raise NotImplementedError("B1: FactorService.list_factors")

    def exposed(self, code: str, min_r2: float = 0.1, limit: int = 20) -> list[Exposure] | None:
        """Companies most exposed to a factor (|beta| descending), None if the factor is unknown."""
        raise NotImplementedError("B1: FactorService.exposed")

    def resolve(self, name: str, limit: int = 5) -> list[FactorSummary]:
        """Fuzzy name to factors (full-text `factor_name_ft`), e.g. 'pétrole' or 'oil' to BRENT."""
        raise NotImplementedError("B1: FactorService.resolve")
