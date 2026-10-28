"""Models for the LangGraph agent (role C) and the Cypher validator endpoint."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.common import DISCLAIMER, TruthLevel


class AskRequest(BaseModel):
    """A question in natural language (French or English)."""

    question: str = Field(
        min_length=3, max_length=500, examples=["Si le Brent prend 20 %, quelles actions PEA en profitent ?"]
    )


class AgentClaim(BaseModel):
    """One statement of the answer, with its level of truth and its evidence."""

    text: str
    truth: TruthLevel
    evidence: list[str] = Field(default_factory=list, description="Refs like Company:FR0000120271")
    reason: str | None = Field(
        default=None, description="Set by the verifier: why the claim was removed or its truth level lowered"
    )


class ToolCall(BaseModel):
    """One tool call made by the agent, in order."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str = ""
    duration_ms: int = 0
    error: str | None = None
    result: Any = Field(
        default=None, description="Full tool result: the verifier checks the answer's numbers against it"
    )


class AgentAnswer(BaseModel):
    """The agent's answer: verified claims, removed claims and the full trace."""

    answer: str
    claims: list[AgentClaim] = Field(default_factory=list)
    removed_claims: list[AgentClaim] = Field(default_factory=list)
    trace: list[ToolCall] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    disclaimer: str = DISCLAIMER


class CypherCheckRequest(BaseModel):
    """A Cypher query to validate (demo of the guardrail)."""

    cypher: str = Field(min_length=1, max_length=4000)


class CypherCheckResult(BaseModel):
    """Validator verdict with the reasons for a rejection."""

    valid: bool
    reasons: list[str] = Field(default_factory=list)
