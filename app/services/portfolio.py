"""Portfolio look-through (role B). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.portfolio import PortfolioRequest, XRayResult


class PortfolioService:
    """Computes what a portfolio is really exposed to."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def xray(self, request: PortfolioRequest) -> XRayResult:
        """Look through funds (Fund-TRACKS->Index-CONSTITUENT->Company) and aggregate.

        One query with UNWIND over the positions. Stocks count directly; funds are spread over the
        index constituents by weight. Unknown ISINs go to `unknown_isins`, never an error.
        Synthetic funds add a warning explaining that HOLDS differs from the tracked exposure.
        """
        raise NotImplementedError("B2: PortfolioService.xray")
