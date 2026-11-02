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
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from neo4j.exceptions import ClientError
from pydantic import BaseModel, Field

from app.agent.guardrails import CypherValidator
from app.database.neo4j import Neo4jClient
from app.models.common import EVIDENCE_PATTERN, ISIN_PATTERN
from app.models.portfolio import PortfolioRequest, Position
from app.models.shocks import Shock, ShockRequest, ShockResult
from app.services.companies import CompanyService, FactorService
from app.services.gds import GdsService
from app.services.hypotheses import HypothesisService
from app.services.portfolio import PortfolioService
from app.services.shocks import MAX_PATH_HOPS, ShockService

RESOLVE_TOOLS = ("resolve_company", "resolve_factor")
# Generated Cypher gets this long on the server (Neo4jClient.read_with_timeout): the guardrail bounds the rows, not
# the work, so a legal but heavy query (a cartesian product) is stopped by Neo4j itself.
CYPHER_TIMEOUT_SECONDS = 5.0

# Argument types with the bounds of the HTTP routes: the model can ask for no more than an API user. A value
# out of bounds fails validation before the service is called, and the error goes back to the model.
Name = Annotated[str, Field(min_length=2, max_length=80)]
Isin = Annotated[str, Field(pattern=ISIN_PATTERN)]
Ref = Annotated[str, Field(pattern=EVIDENCE_PATTERN, max_length=200)]
FactorCode = Annotated[str, Field(min_length=2, max_length=32)]
MinR2 = Annotated[float, Field(ge=0, le=1)]
Limit = Annotated[int, Field(ge=1, le=100)]
ClusterLimit = Annotated[int, Field(ge=1, le=50)]
Neighbours = Annotated[int, Field(ge=1, le=20)]
Hops = Annotated[int, Field(ge=1, le=MAX_PATH_HOPS)]
ShockPct = Annotated[float, Field(ge=-90, le=300)]
Depth = Annotated[int, Field(ge=1, le=3)]
Positions = Annotated[list[Position], Field(min_length=1, max_length=50)]
Status = Annotated[str, Field(pattern=r"^(hypothesis|confirmed|rejected)$")]
CypherText = Annotated[str, Field(min_length=1, max_length=4000)]


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


SHOCK_LINES = 8  # lines of each kind a shock result gives the model


def jsonable(value: Any, truth: str | None = None) -> Any:
    """Pydantic models and lists of them as plain JSON data; `None` becomes `{"found": False}`.

    Every row with an `isin` gets its evidence ref (`Company:<isin>`) and, when `truth` is given and the row has
    none, that truth level: a 7B writer copies these fields far more reliably than it builds them.
    """
    if value is None:
        return {"found": False}
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, list):
        return [jsonable(item, truth) for item in value]
    if isinstance(value, dict):
        value = {key: jsonable(item, truth) if isinstance(item, (list, dict)) else item for key, item in value.items()}
        if isinstance(value.get("isin"), str):
            value.setdefault("ref", f"Company:{value['isin']}")
            if truth:
                value.setdefault("truth", truth)
    return value


def compact_shock(result: ShockResult) -> dict:
    """The part of a shock result the model can use, most useful first.

    The service lists every impacted company, direction-only lines (a declared PRODUCES link, no amount) first:
    cut to a few thousand characters, the model then never saw an amount. Here: the portfolio, its lines by
    euro impact, the largest estimated moves, then only a count of the direction-only lines.
    """
    lines = result.direct + result.indirect
    in_portfolio = sorted(
        (line for line in lines if line.impact_eur is not None), key=lambda line: -abs(line.impact_eur)
    )
    estimated = sorted(
        (line for line in lines if line.impact_eur is None and line.impact_pct is not None),
        key=lambda line: -abs(line.impact_pct),
    )
    direction_only = [line for line in lines if line.impact_pct is None]
    keep = {"isin", "name", "truth", "impact_pct", "impact_eur", "path"}
    return {
        "portfolio_impact_eur": result.portfolio_impact_eur,
        "portfolio_impact_pct": result.portfolio_impact_pct,
        "portfolio_lines": jsonable([line.model_dump(include=keep) for line in in_portfolio[:SHOCK_LINES]]),
        "largest_estimated_moves": jsonable([line.model_dump(include=keep) for line in estimated[:SHOCK_LINES]]),
        "direction_only_count": len(direction_only),
        "direction_only_examples": jsonable([line.model_dump(include=keep) for line in direction_only[:3]]),
        "warnings": result.warnings,
    }


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

    def resolve_company(name: Name) -> dict:
        """Find companies by name or ticker (e.g. 'Total', 'TTE.PA'). Always call it before using an ISIN you
        were not given. If the answer says ambiguous, do not guess: the user is asked which one."""
        candidates = jsonable(services.companies.resolve(name, limit=5))
        return {"candidates": candidates, "ambiguous": ambiguous(name, candidates, ("name", "ticker"))}

    def resolve_factor(name: Name) -> dict:
        """Find a commodity, currency or rate by name (e.g. 'pétrole', 'oil', 'euro dollar', '10-year')."""
        candidates = jsonable(services.factors.resolve(name, limit=5))
        return {"candidates": candidates, "ambiguous": ambiguous(name, candidates, ("name", "code"))}

    def company_exposures(isin: Isin, min_r2: MinR2 = 0.0) -> list:
        """Every factor link of one company: betas (estimate) and declared PRODUCES / CONSUMES (fact)."""
        return jsonable(services.companies.exposures(isin, min_r2=min_r2))

    def most_exposed(factor_code: FactorCode, min_r2: MinR2 = 0.1, limit: Limit = 10) -> Any:
        """Companies whose stock historically moved most with a factor (largest |beta| first)."""
        return jsonable(services.factors.exposed(factor_code, min_r2=min_r2, limit=limit))

    def simulate_shock(
        factor_code: FactorCode, pct: ShockPct, depth: Depth = 2, positions: Positions | None = None
    ) -> dict:
        """Estimated impact of a factor move in % (e.g. BRENT +20) on companies, and on a portfolio when
        positions are given. Rates move in percentage points."""
        request = ShockRequest(shocks=[Shock(factor_code=factor_code, pct=pct)], depth=depth, positions=positions)
        return compact_shock(services.shocks.simulate(request))

    def portfolio_xray(positions: Positions) -> dict:
        """What a portfolio of stocks and ETFs is really exposed to (look-through of the funds)."""
        return jsonable(services.portfolio.xray(PortfolioRequest(positions=positions)))

    def explain_path(from_ref: Ref, to_ref: Ref, max_hops: Hops = 4) -> Any:
        """Shortest explainable path between two refs such as 'Factor:BRENT' and 'Company:FR0000120271'."""
        return jsonable(services.shocks.path(from_ref, to_ref, max_hops=max_hops))

    def clusters(limit: ClusterLimit = 5) -> list:
        """Groups of stocks that move together (Louvain), with the factors most of their members follow."""
        return jsonable(services.gds.clusters(limit=limit), truth="estimate")

    def similar_companies(isin: Isin, k: Neighbours = 5) -> Any:
        """Companies with the most similar position in the correlation network (FastRP embeddings)."""
        return jsonable(services.gds.similar(isin, k=k), truth="estimate")

    def predicted_exposures(isin: Isin) -> Any:
        """Exposures predicted by the ML model where no estimate exists (truth level prediction)."""
        return jsonable(services.gds.predicted_exposures(isin))

    def hypotheses(isin: Isin | None = None, status: Status | None = None, limit: Limit = 10) -> list:
        """Supply-chain relations suggested by news headlines (hypotheses, with confidence and source)."""
        return jsonable(services.hypotheses.list_claims(company_isin=isin, status=status, limit=limit))

    def cypher_fallback(cypher: CypherText) -> dict:
        """Last resort when no other tool fits: a read-only Cypher query, checked by the guardrail first.
        Use parameters-free, bounded queries with a LIMIT of at most 100, written in ASCII only (aliases
        without accents: `AS societe`, not `AS société`)."""
        try:
            verdict = services.validator.validate(cypher)
        except NotImplementedError:
            # Fail closed: without a working validator, generated Cypher never reaches the database.
            return {"rejected": True, "reasons": ["the Cypher validator is not available"]}
        if not verdict.valid:
            return {"rejected": True, "reasons": verdict.reasons}
        try:
            rows = services.client.read_with_timeout(cypher, seconds=CYPHER_TIMEOUT_SECONDS)
        except ClientError as error:
            if "TransactionTimedOut" not in (error.code or ""):
                raise  # any other database error goes to the trace as a tool error
            # Back to the model like a refusal, so it can try a narrower query; never a 500
            return {"rejected": True, "reasons": [f"query stopped after {CYPHER_TIMEOUT_SECONDS:g} s: too much work"]}
        return {"rejected": False, "rows": jsonable(rows)}

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
