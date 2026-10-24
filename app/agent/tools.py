"""Agent tools (role C). Each tool calls exactly one service method and nothing else.

Planned tools: resolve_company, resolve_factor, company_exposures, most_exposed, simulate_shock,
portfolio_xray, explain_path, clusters, similar_companies, predicted_exposures, hypotheses,
cypher_fallback (CypherValidator first, then Neo4jClient.read).
"""

from app.database.neo4j import Neo4jClient


def build_tools(client: Neo4jClient) -> list:
    """Return the LangChain tools bound to this client."""
    raise NotImplementedError("C4: build_tools")
