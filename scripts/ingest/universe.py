"""Step 1 (role A, A1): the investable universe.

Sources: SSGA daily holdings files (index constituents with ISIN, weight, sector, country),
OpenFIGI (ISIN to exchange ticker, mapped to a Yahoo symbol), GLEIF (LEI), and `data/seed/funds.csv`
(hand-checked PEA ETFs with TER, replication and tracked index).
Creates Company, Fund, Index, Sector, Industry, Country and TRACKS, HOLDS, CONSTITUENT, IN_INDUSTRY,
PART_OF, HEADQUARTERED_IN.
"""

from app.config import Settings
from app.database.neo4j import Neo4jClient


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Load the universe into Neo4j and return counts."""
    raise NotImplementedError("A1: universe.run")
