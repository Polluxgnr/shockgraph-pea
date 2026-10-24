"""The only way the application talks to Neo4j.

Every query goes through `Neo4jClient.read` or `Neo4jClient.write`, always with `params`:
never build Cypher with f-strings or string concatenation from user input.
"""

from typing import Any

from fastapi import Request
from neo4j import Driver, GraphDatabase, RoutingControl

from app.config import Settings


class Neo4jClient:
    """Thin wrapper around the Neo4j driver returning plain dictionaries."""

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    @classmethod
    def from_settings(cls, settings: Settings) -> "Neo4jClient":
        """Build a client from the application settings."""
        return cls(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database)

    def read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read query (routed to a reader) and return the records as dictionaries."""
        records, _, _ = self._driver.execute_query(
            query, parameters_=params or {}, database_=self._database, routing_=RoutingControl.READ
        )
        return [record.data() for record in records]

    def write(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a write query and return the records as dictionaries."""
        records, _, _ = self._driver.execute_query(
            query, parameters_=params or {}, database_=self._database, routing_=RoutingControl.WRITE
        )
        return [record.data() for record in records]

    def ping(self) -> bool:
        """Return True when the database answers."""
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def has_gds(self) -> bool:
        """Return True when the Graph Data Science plugin is installed."""
        try:
            self.read("RETURN gds.version() AS version")
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def close(self) -> None:
        """Close the underlying driver."""
        self._driver.close()


def get_client(request: Request) -> Neo4jClient:
    """FastAPI dependency: the client created at startup (overridden in tests)."""
    return request.app.state.neo4j
