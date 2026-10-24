"""Recompute GDS projections and algorithms on the dev database (role C). Usage: python -m scripts.gds_refresh"""

from app.config import get_settings
from app.database.neo4j import Neo4jClient
from app.services.gds import GdsService


def main() -> None:
    """Run GdsService.refresh and print the report."""
    client = Neo4jClient.from_settings(get_settings())
    try:
        print(GdsService(client).refresh().model_dump_json(indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
