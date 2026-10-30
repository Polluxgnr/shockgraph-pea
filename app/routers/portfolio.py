"""Portfolio x-ray and shock endpoints (role B)."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.database.neo4j import Neo4jClient, get_client
from app.models.common import EVIDENCE_PATTERN, ErrorResponse
from app.models.portfolio import PortfolioRequest, XRayResult
from app.models.shocks import PathResult, ShockRequest, ShockResult
from app.services.portfolio import PortfolioService
from app.services.shocks import MAX_PATH_HOPS, ShockService

router = APIRouter(prefix="/api", tags=["portfolio & shocks"])
COMMON_ERRORS = {
    422: {"model": ErrorResponse, "description": "The request body or query parameters failed validation."},
    501: {"model": ErrorResponse, "description": "The requested service is not implemented yet."},
    503: {"model": ErrorResponse, "description": "The graph database is unavailable."},
}
NOT_FOUND = {
    **COMMON_ERRORS,
    404: {"model": ErrorResponse, "description": "A factor or explainable path was not found."},
}

XRAY_EXAMPLES = {
    "stock-and-fund": {
        "summary": "A stock and a PEA ETF",
        "description": "The fund is looked through to the constituents of its tracked index.",
        "value": {
            "positions": [
                {"isin": "FR0011869353", "amount_eur": 1000},
                {"isin": "FR0000120271", "amount_eur": 500},
            ],
            "look_through": True,
        },
    }
}

SHOCK_EXAMPLES = {
    "brent-portfolio": {
        "summary": "Brent rises by 20%",
        "description": "Estimate direct impacts and apply them to an optional portfolio.",
        "value": {
            "shocks": [{"factor_code": "BRENT", "pct": 20}],
            "positions": [{"isin": "FR0000120271", "amount_eur": 1000}],
            "depth": 2,
            "min_r2": 0.1,
        },
    }
}


def get_portfolio_service(client: Neo4jClient = Depends(get_client)) -> PortfolioService:
    """Build the service for one request."""
    return PortfolioService(client)


def get_shock_service(client: Neo4jClient = Depends(get_client)) -> ShockService:
    """Build the service for one request."""
    return ShockService(client)


@router.post("/portfolio/xray", response_model=XRayResult, responses=COMMON_ERRORS)
def xray(
    request: PortfolioRequest = Body(openapi_examples=XRAY_EXAMPLES),
    service: PortfolioService = Depends(get_portfolio_service),
) -> XRayResult:
    """Look through a PEA: real exposure by company, sector, country and factor. Nothing is stored."""
    return service.xray(request)


@router.post("/shocks/simulate", response_model=ShockResult, responses=NOT_FOUND)
def simulate(
    request: ShockRequest = Body(openapi_examples=SHOCK_EXAMPLES),
    service: ShockService = Depends(get_shock_service),
) -> ShockResult:
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
