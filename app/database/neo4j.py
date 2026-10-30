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

    def explain(self, query: str, params: dict[str, Any] | None = None) -> list[str]:
        """Operators of the plan Neo4j would use, depth first, without running the query (for tests and docs)."""
        _, summary, _ = self._driver.execute_query(
            "EXPLAIN " + query, parameters_=params or {}, database_=self._database, routing_=RoutingControl.READ
        )
        return plan_operators(summary.plan)

    def profile(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the query with PROFILE and return what a performance report needs.

        `operators` (depth first), `db_hits` (sum over the plan), `rows` returned and `time_ms` as reported by
        the server. The query is routed as a read, so Neo4j refuses any write (`Neo.ClientError.Statement.AccessMode`)
        and nothing is created; still never call it with user input.
        """
        records, summary, _ = self._driver.execute_query(
            "PROFILE " + query, parameters_=params or {}, database_=self._database, routing_=RoutingControl.READ
        )
        plan = summary.profile or {}
        return {
            "operators": plan_operators(plan),
            "db_hits": plan_total(plan, "dbHits"),
            "rows": len(records),
            "time_ms": (summary.result_available_after or 0) + (summary.result_consumed_after or 0),
        }

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


def plan_operators(plan: Any) -> list[str]:
    """Operator names of an EXPLAIN/PROFILE plan, depth first, without the planner suffix (`@neo4j`)."""
    if not plan:
        return []
    node = plan if isinstance(plan, dict) else {"operatorType": plan.operator_type, "children": plan.children}
    name = str(node.get("operatorType", "")).split("@", 1)[0]
    return [name] + [operator for child in node.get("children", []) for operator in plan_operators(child)]


def plan_total(plan: Any, key: str) -> int:
    """Sum of a numeric field (e.g. `dbHits`) over every operator of a PROFILE plan."""
    if not plan:
        return 0
    return int(plan.get(key, 0) or 0) + sum(plan_total(child, key) for child in plan.get("children", []))


def get_client(request: Request) -> Neo4jClient:
    """FastAPI dependency: the client created at startup (overridden in tests)."""
    return request.app.state.neo4j
