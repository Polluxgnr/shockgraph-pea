"""LangGraph agent (role C): router, planner, tools, writer, verifier."""

from app.database.neo4j import Neo4jClient
from app.models.agent import AgentAnswer

MAX_TOOL_CALLS = 6


class ShockAgent:
    """Answers questions about exposures and shocks using graph tools only.

    Nodes: router (out of scope or advice request leads to a refusal with a reason; ambiguous name
    leads to a clarification question), planner (large model with bound tools, at most MAX_TOOL_CALLS
    calls), tools, writer (structured `AgentAnswer`), verifier (app/agent/verifier.py).
    """

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def ask(self, question: str) -> AgentAnswer:
        """Run the graph on one question and return the verified answer with its trace."""
        raise NotImplementedError("C4: ShockAgent.ask")
