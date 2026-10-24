"""Answer verifier (role C): nothing reaches the user that the trace does not support."""

from app.database.neo4j import Neo4jClient
from app.models.agent import AgentAnswer


class AnswerVerifier:
    """Moves unsupported claims to `removed_claims`.

    Rules: every evidence ref must exist in the database; every number in a claim must appear in a
    tool result of the trace; the truth level must match the tool that produced it (a beta is an
    `estimate`, a Claim is a `hypothesis`, a PREDICTED_SENSITIVITY is a `prediction`).
    """

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def verify(self, answer: AgentAnswer) -> AgentAnswer:
        """Return a copy of the answer with unsupported claims removed."""
        raise NotImplementedError("C5: AnswerVerifier.verify")
