"""Agent endpoints (role C): the question endpoint and the Cypher guardrail demo."""

from fastapi import APIRouter, Depends

from app.agent.graph import ShockAgent
from app.agent.guardrails import CypherValidator
from app.database.neo4j import Neo4jClient, get_client
from app.models.agent import AgentAnswer, AskRequest, CypherCheckRequest, CypherCheckResult
from app.security import require_api_key

router = APIRouter(prefix="/api", tags=["agent"])


def get_agent(client: Neo4jClient = Depends(get_client)) -> ShockAgent:
    """Build the agent for one request."""
    return ShockAgent(client)


@router.post("/ask", response_model=AgentAnswer, dependencies=[Depends(require_api_key)], responses={401: {}})
def ask(request: AskRequest, agent: ShockAgent = Depends(get_agent)) -> AgentAnswer:
    """Ask a question in natural language. The answer separates facts, estimates, predictions and hypotheses."""
    return agent.ask(request.question)


@router.post("/agent/validate-cypher", response_model=CypherCheckResult)
def validate_cypher(request: CypherCheckRequest) -> CypherCheckResult:
    """Run the Cypher guardrail on a query (it is never executed)."""
    result = CypherValidator().validate(request.cypher)
    return CypherCheckResult(valid=result.valid, reasons=result.reasons)
