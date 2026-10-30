"""Graph Data Science endpoints (role C)."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.database.neo4j import Neo4jClient, get_client
from app.models.common import ISIN_PATTERN, ErrorResponse
from app.models.companies import Exposure
from app.models.network import Cluster, GdsReport, RankedCompany, SimilarCompany
from app.security import require_api_key
from app.services.gds import GdsService, RefreshInProgress

router = APIRouter(prefix="/api", tags=["network (GDS & ML)"])
NOT_FOUND = {404: {"model": ErrorResponse}}


def get_gds_service(client: Neo4jClient = Depends(get_client)) -> GdsService:
    """Build the service for one request."""
    return GdsService(client)


@router.get("/network/clusters", response_model=list[Cluster])
def clusters(
    limit: int = Query(default=10, ge=1, le=50), service: GdsService = Depends(get_gds_service)
) -> list[Cluster]:
    """Communities of stocks that move together (Louvain)."""
    return service.clusters(limit=limit)


@router.get("/network/systemic", response_model=list[RankedCompany])
def systemic(
    limit: int = Query(default=20, ge=1, le=100), service: GdsService = Depends(get_gds_service)
) -> list[RankedCompany]:
    """Most systemic companies (PageRank)."""
    return service.systemic(limit=limit)


@router.post(
    "/network/refresh",
    response_model=GdsReport,
    dependencies=[Depends(require_api_key)],
    responses={401: {}, 409: {"model": ErrorResponse}},
)
def refresh(service: GdsService = Depends(get_gds_service)) -> GdsReport:
    """Recompute projections and algorithms (writes to the graph: API key required).

    409 when another refresh holds the lock (another worker or `make gds`): nothing changed, retry later.
    """
    try:
        return service.refresh()
    except RefreshInProgress as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/companies/{isin}/similar", response_model=list[SimilarCompany], responses=NOT_FOUND)
def similar(
    isin: str = Path(pattern=ISIN_PATTERN),
    k: int = Query(default=5, ge=1, le=20),
    service: GdsService = Depends(get_gds_service),
) -> list[SimilarCompany]:
    """Companies with the most similar position in the graph (FastRP embeddings)."""
    result = service.similar(isin, k=k)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown company {isin}")
    return result


@router.get("/companies/{isin}/predicted-exposures", response_model=list[Exposure], responses=NOT_FOUND)
def predicted_exposures(
    isin: str = Path(pattern=ISIN_PATTERN),
    limit: int = Query(default=10, ge=1, le=50),
    service: GdsService = Depends(get_gds_service),
) -> list[Exposure]:
    """Exposures predicted by the ML model (truth level `prediction`)."""
    result = service.predicted_exposures(isin, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown company {isin}")
    return result
