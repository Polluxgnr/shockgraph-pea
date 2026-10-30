"""FastAPI application: wiring, lifespan and error handlers. Every route is declared here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable

from app.config import get_settings
from app.database.neo4j import Neo4jClient
from app.routers import agent, companies, health, hypotheses, network, portfolio

DESCRIPTION = """
**ShockGraph PEA** maps PEA-eligible stocks, sectors, commodities, currencies and rates as a graph,
and answers: *if oil goes up 20 %, what happens to my PEA, and through which links?*

Every number carries a level of truth: `fact`, `estimate`, `prediction` or `hypothesis`.
Educational tool, not investment advice.
"""


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Open the Neo4j driver at startup and close it at shutdown."""
    if not hasattr(application.state, "neo4j"):
        application.state.neo4j = Neo4jClient.from_settings(get_settings())
    yield
    application.state.neo4j.close()


app = FastAPI(title="ShockGraph PEA", version=get_settings().app_version, description=DESCRIPTION, lifespan=lifespan)

for module in (health, companies, portfolio, network, hypotheses, agent):
    app.include_router(module.router)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a compact 422 response while retaining field-level validation details."""
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"] if part != "body"),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": errors},
    )


@app.exception_handler(NotImplementedError)
async def not_implemented_handler(_: Request, exc: NotImplementedError) -> JSONResponse:
    """A service method that is not written yet answers 501 with the task id (e.g. 'B1: ...')."""
    return JSONResponse(status_code=501, content={"detail": f"Not implemented yet ({exc})"})


@app.exception_handler(ServiceUnavailable)
async def neo4j_unavailable_handler(_: Request, _exc: ServiceUnavailable) -> JSONResponse:
    """Neo4j is down or unreachable."""
    return JSONResponse(status_code=503, content={"detail": "Graph database unavailable"})
