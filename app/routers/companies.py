"""Company and factor endpoints (role B)."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.database.neo4j import Neo4jClient, get_client
from app.models.common import ISIN_PATTERN, ErrorResponse, Page
from app.models.companies import CompanyDetail, CompanySummary, Exposure, FactorSummary
from app.services.companies import CompanyService, FactorService

router = APIRouter(prefix="/api", tags=["companies & factors"])
NOT_FOUND = {404: {"model": ErrorResponse}}


def get_company_service(client: Neo4jClient = Depends(get_client)) -> CompanyService:
    """Build the service for one request."""
    return CompanyService(client)


def get_factor_service(client: Neo4jClient = Depends(get_client)) -> FactorService:
    """Build the service for one request."""
    return FactorService(client)


@router.get("/companies", response_model=Page[CompanySummary])
def search_companies(
    query: str | None = Query(default=None, alias="q", min_length=2, max_length=80, description="Name or ticker"),
    sector: str | None = Query(default=None, max_length=40),
    pea_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=200),
    service: CompanyService = Depends(get_company_service),
) -> Page[CompanySummary]:
    """Search companies, paginated with a cursor."""
    return service.search(query=query, sector=sector, pea_only=pea_only, limit=limit, cursor=cursor)


@router.get("/companies/{isin}", response_model=CompanyDetail, responses=NOT_FOUND)
def get_company(
    isin: str = Path(pattern=ISIN_PATTERN), service: CompanyService = Depends(get_company_service)
) -> CompanyDetail:
    """One company with its sector, industry, country and indexes."""
    company = service.get(isin)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown company {isin}")
    return company


@router.get("/companies/{isin}/exposures", response_model=list[Exposure], responses=NOT_FOUND)
def company_exposures(
    isin: str = Path(pattern=ISIN_PATTERN),
    min_r2: float = Query(default=0.0, ge=0, le=1),
    service: CompanyService = Depends(get_company_service),
) -> list[Exposure]:
    """What a company is exposed to: declared facts and statistical estimates, labelled."""
    if service.get(isin) is None:
        raise HTTPException(status_code=404, detail=f"Unknown company {isin}")
    return service.exposures(isin, min_r2=min_r2)


@router.get("/factors", response_model=list[FactorSummary])
def list_factors(service: FactorService = Depends(get_factor_service)) -> list[FactorSummary]:
    """Every commodity, currency and rate in the graph."""
    return service.list_factors()


@router.get("/factors/{code}/exposed", response_model=list[Exposure], responses=NOT_FOUND)
def most_exposed(
    code: str = Path(min_length=2, max_length=32),
    min_r2: float = Query(default=0.1, ge=0, le=1),
    limit: int = Query(default=20, ge=1, le=100),
    service: FactorService = Depends(get_factor_service),
) -> list[Exposure]:
    """Companies most exposed to a factor."""
    result = service.exposed(code, min_r2=min_r2, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown factor {code}")
    return result
