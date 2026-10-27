"""Agent tools (role C). Each tool calls exactly one service method and nothing else.

Planned tools: resolve_company, resolve_factor, company_exposures, most_exposed, simulate_shock,
portfolio_xray, explain_path, clusters, similar_companies, predicted_exposures, hypotheses,
cypher_fallback (CypherValidator first, then Neo4jClient.read).

The tools never compute a number themselves: what the model reads is exactly what a service returned, so
the verifier (C5) can check every number of the answer against the trace. Typed tools come first; generated
Cypher is the fallback and is refused unless the validator accepts it.
"""

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agent.guardrails import CypherValidator
from app.database.neo4j import Neo4jClient
from app.models.portfolio import PortfolioRequest, Position
from app.models.shocks import Shock, ShockRequest
from app.services.companies import CompanyService, FactorService
from app.services.gds import GdsService
from app.services.hypotheses import HypothesisService
from app.services.portfolio import PortfolioService
from app.services.shocks import ShockService

RESOLVE_TOOLS = ("resolve_company", "resolve_factor")


@dataclass
class Services:  # pylint: disable=too-many-instance-attributes
    """Everything the tools may call (one field per service). Tests replace any of them with a fake."""

    client: Neo4jClient
    companies: CompanyService
    factors: FactorService
    portfolio: PortfolioService
    shocks: ShockService
    gds: GdsService
    hypotheses: HypothesisService
    validator: CypherValidator

    @classmethod
    def for_client(cls, client: Neo4jClient) -> "Services":
        """The real services on one Neo4j client."""
        return cls(
            client=client,
            companies=CompanyService(client),
            factors=FactorService(client),
            portfolio=PortfolioService(client),
            shocks=ShockService(client),
            gds=GdsService(client),
            hypotheses=HypothesisService(client),
            validator=CypherValidator(),
        )


def build_tools(client: Neo4jClient) -> list:
    """Return the LangChain tools bound to this client."""
    return build_tools_for(Services.for_client(client))


def jsonable(value: Any) -> Any:
    """Pydantic models and lists of them as plain JSON data; `None` becomes `{"found": False}`."""
    if value is None:
        return {"found": False}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


LEGAL_SUFFIXES = {"sa", "se", "ag", "nv", "plc", "inc", "corp", "ltd", "spa", "asa", "ab", "oyj", "sca", "co"}


def _normalise(text: str | None) -> str:
    """Lower case, letters and digits only, legal suffixes dropped: 'TotalEnergies SE' -> 'totalenergies'."""
    words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
    return " ".join(word for word in words if word not in LEGAL_SUFFIXES)


def ambiguous(name: str, candidates: list[dict], keys: tuple[str, ...]) -> bool:
    """True when several candidates come back and not exactly one of them is the name asked for.

    Only an exact match (name without its legal suffix, ticker or code) counts. A prefix does not: 'Total'
    starts 'Total Gabon' as a whole word, but the user most likely means TotalEnergies. Asking costs one
    question; guessing the wrong company gives a confident wrong answer.
    """
    if len(candidates) < 2:
        return False
    wanted = _normalise(name)
    exact = [candidate for candidate in candidates if any(_normalise(candidate.get(key)) == wanted for key in keys)]
    return len(exact) != 1


def build_tools_for(services: Services) -> list[StructuredTool]:
    """The 12 tools on the given services (see the module docstring)."""

    def resolve_company(name: str) -> dict:
        """Find companies by name or ticker (e.g. 'Total', 'TTE.PA'). Always call it before using an ISIN you
        were not given. If the answer says ambiguous, do not guess: the user is asked which one."""
        candidates = jsonable(services.companies.resolve(name, limit=5))
        return {"candidates": candidates, "ambiguous": ambiguous(name, candidates, ("name", "ticker"))}

    def resolve_factor(name: str) -> dict:
        """Find a commodity, currency or rate by name (e.g. 'pétrole', 'oil', 'euro dollar', '10-year')."""
        candidates = jsonable(services.factors.resolve(name, limit=5))
        return {"candidates": candidates, "ambiguous": ambiguous(name, candidates, ("name", "code"))}

    def company_exposures(isin: str, min_r2: float = 0.0) -> list:
        """Every factor link of one company: betas (estimate) and declared PRODUCES / CONSUMES (fact)."""
        return jsonable(services.companies.exposures(isin, min_r2=min_r2))

    def most_exposed(factor_code: str, min_r2: float = 0.1, limit: int = 10) -> Any:
        """Companies whose stock historically moved most with a factor (largest |beta| first)."""
        return jsonable(services.factors.exposed(factor_code, min_r2=min_r2, limit=limit))

    def simulate_shock(factor_code: str, pct: float, depth: int = 2, positions: list[Position] | None = None) -> dict:
        """Estimated impact of a factor move in % (e.g. BRENT +20) on companies, and on a portfolio when
        positions are given. Rates move in percentage points."""
        request = ShockRequest(shocks=[Shock(factor_code=factor_code, pct=pct)], depth=depth, positions=positions)
        return jsonable(services.shocks.simulate(request))

    def portfolio_xray(positions: list[Position]) -> dict:
        """What a portfolio of stocks and ETFs is really exposed to (look-through of the funds)."""
        return jsonable(services.portfolio.xray(PortfolioRequest(positions=positions)))

    def explain_path(from_ref: str, to_ref: str, max_hops: int = 4) -> Any:
        """Shortest explainable path between two refs such as 'Factor:BRENT' and 'Company:FR0000120271'."""
        return jsonable(services.shocks.path(from_ref, to_ref, max_hops=max_hops))

    def clusters(limit: int = 5) -> list:
        """Groups of stocks that move together (Louvain), with the factors most of their members follow."""
        return jsonable(services.gds.clusters(limit=limit))

    def similar_companies(isin: str, k: int = 5) -> Any:
        """Companies with the most similar position in the correlation network (FastRP embeddings)."""
        return jsonable(services.gds.similar(isin, k=k))

    def predicted_exposures(isin: str) -> Any:
        """Exposures predicted by the ML model where no estimate exists (truth level prediction)."""
        return jsonable(services.gds.predicted_exposures(isin))

    def hypotheses(isin: str | None = None, status: str | None = None, limit: int = 10) -> list:
        """Supply-chain relations suggested by news headlines (hypotheses, with confidence and source)."""
        return jsonable(services.hypotheses.list_claims(company_isin=isin, status=status, limit=limit))

    def cypher_fallback(cypher: str) -> dict:
        """Last resort when no other tool fits: a read-only Cypher query, checked by the guardrail first.
        Use parameters-free, bounded queries with a LIMIT of at most 100."""
        try:
            verdict = services.validator.validate(cypher)
        except NotImplementedError:
            # Fail closed: without a working validator, generated Cypher never reaches the database.
            return {"rejected": True, "reasons": ["the Cypher validator is not available"]}
        if not verdict.valid:
            return {"rejected": True, "reasons": verdict.reasons}
        return {"rejected": False, "rows": jsonable(services.client.read(cypher))}

    functions = [
        resolve_company,
        resolve_factor,
        company_exposures,
        most_exposed,
        simulate_shock,
        portfolio_xray,
        explain_path,
        clusters,
        similar_companies,
        predicted_exposures,
        hypotheses,
        cypher_fallback,
    ]
    return [StructuredTool.from_function(function) for function in functions]
