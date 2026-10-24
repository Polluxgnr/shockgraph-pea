"""Step 2 (role A, A2): declared exposures, truth level `fact`.

Sources: Wikidata SPARQL by ISIN (P946): industry, products (P1056), parent organisation;
`data/seed/industry_factors.csv`: hand-written industry to commodity map (airlines CONSUMES BRENT...).
Creates Factor nodes (from `data/seed/factors.csv`) and PRODUCES / CONSUMES links.
"""

from app.config import Settings
from app.database.neo4j import Neo4jClient


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Load factors and declared exposures; return counts."""
    raise NotImplementedError("A2: declared.run")
