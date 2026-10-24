"""Neo4j access layer."""

from app.database.neo4j import Neo4jClient, get_client

__all__ = ["Neo4jClient", "get_client"]
