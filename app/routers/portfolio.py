"""Portfolio x-ray and shock endpoints (role B)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database.neo4j import Neo4jClient, get_client
from app.models.common import EVIDENCE_PATTERN, ErrorResponse
from app.models.portfolio import PortfolioRequest, XRayResult
from app.models.shocks import PathResult, ShockRequest, ShockResult
from app.services.portfolio import PortfolioService
from app.services.shocks import MAX_PATH_HOPS, ShockService

router = APIRouter(prefix="/api", tags=["portfolio & shocks"])
NOT_FOUND = {404: {"model": ErrorResponse}}


def get_portfolio_service(client: Neo4jClient = Depends(get_client)) -> PortfolioService:
    """Build the service for one request."""
    return PortfolioService(client)


def get_shock_service(client: Neo4jClient = Depends(get_client)) -> ShockService:
    """Build the service for one request."""
    return ShockService(client)


@router.post("/portfolio/xray", response_model=XRayResult)
def xray(request: PortfolioRequest, service: PortfolioService = Depends(get_portfolio_service)) -> XRayResult:
    """Look through a PEA: real exposure by company, sector, country and factor. Nothing is stored."""
    return service.xray(request)


@router.post("/shocks/simulate", response_model=ShockResult, responses=NOT_FOUND)
def simulate(request: ShockRequest, service: ShockService = Depends(get_shock_service)) -> ShockResult:
    """Propagate one or several factor shocks to companies (and to a portfolio when given)."""
    try:
        return service.simulate(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown factor {exc.args[0]}") from exc


@router.get("/paths", response_model=PathResult, responses=NOT_FOUND)
def explain_path(
    from_ref: str = Query(alias="from", pattern=EVIDENCE_PATTERN, examples=["Factor:BRENT"]),
    to_ref: str = Query(alias="to", pattern=EVIDENCE_PATTERN, examples=["Company:FR0000120271"]),
    max_hops: int = Query(default=4, ge=1, le=MAX_PATH_HOPS),
    service: ShockService = Depends(get_shock_service),
) -> PathResult:
    """Shortest explainable path between two nodes, with the truth level of each hop."""
    result = service.path(from_ref, to_ref, max_hops=max_hops)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No path from {from_ref} to {to_ref} within {max_hops} hops")
    return result
