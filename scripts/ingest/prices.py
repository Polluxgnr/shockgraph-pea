"""Step 3 (role C, C1): prices, sensitivities and correlations, truth level `estimate`.

Sources: Yahoo Finance (stocks, commodity futures, FX), FRED and ECB Data Portal (rates).
Weekly returns over `returns_window_years`, cached in `data/cache/*.parquet`.
SENSITIVE_TO: OLS of stock returns on factor returns, controlling for the market.
CORRELATED_WITH: top-k neighbours per stock on residual returns, rho above a threshold.
"""

from app.config import Settings
from app.database.neo4j import Neo4jClient


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Download, compute and write the estimates; return counts."""
    raise NotImplementedError("C1: prices.run")
