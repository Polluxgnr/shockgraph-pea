"""Health endpoint, used by Docker health checks and the container tests."""

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.database.neo4j import Neo4jClient, get_client
from app.models.common import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(client: Neo4jClient = Depends(get_client), settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Report whether Neo4j (and GDS) answer."""
    neo4j_ok = client.ping()
    return HealthResponse(
        status="ok" if neo4j_ok else "degraded",
        neo4j=neo4j_ok,
        gds=client.has_gds() if neo4j_ok else None,
        version=settings.app_version,
    )
