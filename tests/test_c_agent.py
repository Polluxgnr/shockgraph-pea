"""C4: the agent with a scripted model and fake services (no real LLM, no real service needed)."""

import pytest
from langchain_core.messages import AIMessage
from langchain_ollama import ChatOllama

from app.agent import graph as agent_graph
from app.agent.graph import MAX_TOOL_CALLS, ShockAgent, is_advice_request
from app.agent.guardrails import ValidationResult
from app.agent.tools import Services, ambiguous, build_tools, compact_shock, jsonable
from app.config import Settings
from app.llm.client import ScriptedChatModel, get_chat_model, tool_call
from app.models.companies import CompanySummary, Exposure, FactorSummary
from app.models.shocks import ImpactLine, ShockResult
from tests.conftest import FakeClient

PETROLIA = CompanySummary(isin="FRTEST000011", name="Petrolia SA", ticker="PTL.PA", sector="Energy", country="FR")
ORSAY = CompanySummary(isin="FRTEST000052", name="Banque Orsay", ticker="ORS.PA", sector="Financials", country="FR")
OTHER_BANK = CompanySummary(isin="FRTEST000099", name="Banque Lumiere", ticker="LUM.PA", country="FR")
BRENT = FactorSummary(code="BRENT", name="Brent crude oil", kind="commodity", unit="USD/bbl")
SHOCK = ShockResult(
    direct=[
        ImpactLine(
            isin="FRTEST000011",
            name="Petrolia SA",
            truth="estimate",
            impact_pct=12.4,
            path=["Factor:BRENT", "Company:FRTEST000011"],
        )
    ]
)


class FakeCompanies:
    """CompanyService stand-in."""

    def resolve(self, name, limit=5):
        """Two banks for 'Banque', Petrolia otherwise."""
        del limit
        return [ORSAY, OTHER_BANK] if name.lower().startswith("banque") else [PETROLIA]

    def exposures(self, isin, min_r2=0.0):
        """One estimate."""
        del min_r2
        return [
            Exposure(
                company_isin=isin,
                company_name="Petrolia SA",
                factor_code="BRENT",
                factor_name="Brent crude oil",
                relation="SENSITIVE_TO",
                truth="estimate",
                beta=0.62,
                r2=0.48,
            )
        ]


class FakeFactors:
    """FactorService stand-in."""

    def resolve(self, name, limit=5):
        """Brent for anything oil-like."""
        del name, limit
        return [BRENT]


class FakeShocks:
    """ShockService stand-in."""

    def __init__(self):
        self.requests = []

    def simulate(self, request):
        """Record the request, return the fixture's BRENT +20 result."""
        self.requests.append(request)
        return SHOCK


class NotWritten:
    """A service of another role that is not implemented yet."""

    def __getattr__(self, name):
        def missing(*args, **kwargs):
            del args, kwargs
            raise NotImplementedError(f"B2: {name}")

        return missing


class PassThroughVerifier:
    """AnswerVerifier stand-in: these tests check the agent, tests/test_c_verifier.py checks the verifier."""

    def verify(self, answer):
        """Return the answer unchanged."""
        return answer


class FakeValidator:
    """CypherValidator stand-in: refuses anything that writes."""

    def validate(self, cypher):
        """Valid unless the query contains DELETE."""
        if "DELETE" in cypher.upper():
            return ValidationResult(False, ["write clause DELETE is not allowed"])
        return ValidationResult(True, [])


def make_agent(large_script=None, small_script=None, validator=None) -> tuple[ShockAgent, FakeClient]:
    """An agent on fakes, with scripted small and large models."""
    client = FakeClient(rows=[{"name": "Petrolia SA"}])
    agent = ShockAgent(client)
    agent.verifier = PassThroughVerifier()
    agent.services = Services(
        client=client,
        companies=FakeCompanies(),
        factors=FakeFactors(),
        portfolio=NotWritten(),
        shocks=FakeShocks(),
        gds=NotWritten(),
        hypotheses=NotWritten(),
        validator=validator or FakeValidator(),
    )
    agent.small = ScriptedChatModel(script=list(small_script or [{"route": "answer", "reason": "shock question"}]))
    agent.large = ScriptedChatModel(script=list(large_script or []))
    return agent, client


WRITTEN = {
    "answer": "Si le Brent prend 20 %, Petrolia a historiquement monté d'environ 12.4 %.",
    "claims": [
        {
            "text": "Petrolia: +12.4 % (estimation)",
            "truth": "estimate",
            "evidence": ["Company:FRTEST000011", "Factor:BRENT"],
        }
    ],
}


# --- full runs -----------------------------------------------------------------------------------


def test_successful_question_with_trace():
    """resolve_factor then simulate_shock, then a structured answer; every call is in the trace."""
    agent, _ = make_agent(
        large_script=[
            tool_call("resolve_factor", {"name": "Brent"}),
            tool_call("simulate_shock", {"factor_code": "BRENT", "pct": 20}),
            AIMessage(content="Petrolia gains about 12.4 %."),
            WRITTEN,
        ]
    )
    answer = agent.ask("Si le Brent prend 20 %, quelles actions PEA en profitent ?")

    assert not answer.refused
    assert [call.tool for call in answer.trace] == ["resolve_factor", "simulate_shock"]
    assert all(call.error is None and call.duration_ms >= 0 for call in answer.trace)
    assert "12.4" in answer.trace[1].result_preview
    assert answer.trace[1].result["largest_estimated_moves"][0]["impact_pct"] == 12.4
    assert answer.trace[1].result["largest_estimated_moves"][0]["ref"] == "Company:FRTEST000011"
    assert answer.claims[0].truth == "estimate" and answer.claims[0].evidence[1] == "Factor:BRENT"
    assert answer.disclaimer
    assert agent.services.shocks.requests[0].shocks[0].pct == 20
    assert not agent.large.script  # every scripted step was used


@pytest.mark.parametrize(
    "question",
    [
        "Dois-je acheter Petrolia avant la hausse du pétrole ?",
        "Should I buy TotalEnergies now?",
        "Faut-il vendre mes actions Airbus ?",
        "Est-ce que tu me conseilles LVMH ?",
        "Is it a good time to invest in banks?",
        "Buy or sell Petrolia?",
        # direct orders, from the review of #31 (the first one is also a B7 evaluation trap)
        "Tell me to sell all my ETFs.",
        "Buy Petrolia today.",
        "Vends toutes mes actions.",
        "Dis-moi quoi acheter.",
        "Please sell my airline stocks.",
        "Oil is up. Sell Air France?",
        "Which ETF should I pick? Tell me which one to buy.",
        "What are the best stocks to buy before the oil shock?",
        "Achète du TotalEnergies.",
        "Quelles actions acheter si le Brent monte ?",
    ],
)
def test_advice_is_refused_before_any_model_call(question):
    """A recommendation request is refused by rule, with an educational redirect; no model is called."""
    agent, _ = make_agent()
    answer = agent.ask(question)
    assert answer.refused and answer.refusal_reason == "investment advice"
    assert not answer.trace and not agent.small.prompts and not agent.large.prompts
    assert "simul" in answer.answer.lower()


@pytest.mark.parametrize(
    "question",
    [
        "What happens to TotalEnergies if oil rises 20 %?",
        "Quelles actions de mon PEA sont exposées au cuivre ?",
        "Which stocks move like Airbus?",
        # the same verbs, but about what companies do, not what the user should do
        "Which companies buy copper from Cuprum?",
        "Who sells naphtha to Helios Chimie?",
        "What happened to airlines in the oil sell-off?",
        "Quelles entreprises achètent du cuivre ?",
        "Si le pétrole monte, quelles sociétés vendent plus cher ?",
        "Show me the companies most exposed to gas.",
    ],
)
def test_ordinary_questions_are_not_advice(question):
    """Exposure and shock questions pass the advice rule."""
    assert not is_advice_request(question)


def test_out_of_scope_is_refused_with_the_reason():
    """The small model says out of scope: refusal, no planning."""
    agent, _ = make_agent(small_script=[{"route": "out_of_scope", "reason": "weather question"}])
    answer = agent.ask("Quel temps fera-t-il demain à Lyon ?")
    assert answer.refused and answer.refusal_reason == "weather question"
    assert "périmètre" in answer.answer
    assert not agent.large.prompts


def test_ambiguous_name_asks_which_one():
    """Two banks match 'Banque': the agent asks instead of guessing, and stops there."""
    agent, _ = make_agent(large_script=[tool_call("resolve_company", {"name": "Banque"}), WRITTEN])
    answer = agent.ask("What is Banque exposed to?")
    assert not answer.refused and not answer.claims
    assert "Banque Orsay (FRTEST000052)" in answer.answer and "Banque Lumiere (FRTEST000099)" in answer.answer
    assert [call.tool for call in answer.trace] == ["resolve_company"]
    assert len(agent.large.prompts) == 1  # no second planning step, no writer


def test_generated_cypher_write_is_rejected_and_never_run():
    """cypher_fallback goes through the validator: a DELETE is refused and the database is not called."""
    cypher = "MATCH (c:Company) DETACH DELETE c"
    agent, client = make_agent(large_script=[tool_call("cypher_fallback", {"cypher": cypher})])
    answer = agent.ask("Clean up the companies, please")
    result = answer.trace[0].result
    assert result == {"rejected": True, "reasons": ["write clause DELETE is not allowed"]}
    assert not client.queries


def test_generated_cypher_is_refused_while_the_validator_is_missing():
    """Fail closed: a validator that is not implemented yet refuses everything."""

    class MissingValidator:
        """CypherValidator before A4 is merged."""

        def validate(self, cypher):
            """Not written yet."""
            raise NotImplementedError(f"A4: {cypher}")

    query = "MATCH (c:Company) RETURN c.name LIMIT 1"
    agent, client = make_agent(
        large_script=[tool_call("cypher_fallback", {"cypher": query})], validator=MissingValidator()
    )
    answer = agent.ask("Name one company")
    assert answer.trace[0].result["rejected"] is True and not client.queries


def test_valid_generated_cypher_runs_through_the_client():
    """An accepted query is run with Neo4jClient.read and its rows go to the trace."""
    query = "MATCH (c:Company) RETURN c.name AS name LIMIT 1"
    agent, client = make_agent(large_script=[tool_call("cypher_fallback", {"cypher": query})])
    answer = agent.ask("Name one company")
    assert answer.trace[0].result == {"rejected": False, "rows": [{"name": "Petrolia SA"}]}
    assert client.queries[0][0] == query


class Recorder:
    """Any service: records every method call, returns an empty list."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            del args, kwargs
            self.calls.append(name)
            return []

        return method


@pytest.mark.parametrize(
    "tool, args",
    [
        ("most_exposed", {"factor_code": "BRENT", "limit": 1_000_000}),
        ("most_exposed", {"factor_code": "BRENT", "min_r2": -5}),
        ("clusters", {"limit": 1_000}),
        ("hypotheses", {"limit": 0}),
        ("hypotheses", {"status": "anything"}),
        ("similar_companies", {"isin": "FRTEST000011", "k": 500}),
        ("company_exposures", {"isin": "not an isin"}),
        ("explain_path", {"from_ref": "Factor:BRENT", "to_ref": "Company:FRTEST000011", "max_hops": 50}),
        ("explain_path", {"from_ref": "Planet:Mars", "to_ref": "Company:FRTEST000011"}),
        ("simulate_shock", {"factor_code": "BRENT", "pct": 5_000}),
        ("simulate_shock", {"factor_code": "BRENT", "pct": 20, "depth": 9}),
        ("resolve_company", {"name": "x" * 500}),
        ("cypher_fallback", {"cypher": "x" * 5_000}),
    ],
)
def test_out_of_bounds_arguments_never_reach_the_service(tool, args):
    """Review of #31: the tool arguments have the bounds of the HTTP routes; validation fails first."""
    recorder = Recorder()
    services = Services(recorder, recorder, recorder, recorder, recorder, recorder, recorder, recorder)
    tools = {item.name: item for item in agent_graph.build_tools_for(services)}
    with pytest.raises(ValueError):  # pydantic's ValidationError is a ValueError
        tools[tool].invoke(args)
    assert recorder.calls == []


def test_an_out_of_bounds_call_in_the_graph_becomes_an_error_for_the_model():
    """The model asks for a million rows: the trace says why it failed, the service is never called."""
    huge = tool_call("most_exposed", {"factor_code": "BRENT", "limit": 1_000_000})
    agent, _ = make_agent(large_script=[huge, AIMessage(""), WRITTEN])
    agent.services.factors = Recorder()
    answer = agent.ask("Which stocks moved most with Brent?")
    assert "less than or equal to 100" in answer.trace[0].error
    assert agent.services.factors.calls == []


def test_tool_budget_is_enforced():
    """Eight calls in one step: six run, two are skipped and say why; then the writer runs."""
    calls = AIMessage(
        content="",
        tool_calls=[{"name": "resolve_factor", "args": {"name": f"oil {i}"}, "id": f"c{i}"} for i in range(8)],
    )
    agent, _ = make_agent(large_script=[calls, WRITTEN])
    answer = agent.ask("Tell me everything about oil")
    assert len(answer.trace) == 8
    assert sum(call.error is None for call in answer.trace) == MAX_TOOL_CALLS
    assert answer.trace[-1].error.startswith("skipped: limit of 6")
    assert answer.claims  # the writer ran on what was collected


def test_missing_service_is_reported_not_fatal():
    """A service of another role that raises NotImplementedError becomes an error in the trace."""
    agent, _ = make_agent(large_script=[tool_call("clusters", {"limit": 3}), AIMessage(content=""), WRITTEN])
    answer = agent.ask("Which groups of stocks move together?")
    assert answer.trace[0].error.startswith("not implemented yet")
    assert answer.claims


def test_unusable_writer_output_gives_a_safe_answer():
    """When the model returns no usable structured answer, the agent says so and makes no claim."""
    agent, _ = make_agent(large_script=[tool_call("resolve_factor", {"name": "oil"})])
    answer = agent.ask("What is oil?")
    assert answer.claims == [] and "could not write a reliable answer" in answer.answer
    assert "resolve_factor" in answer.answer


def test_model_down_gives_a_safe_answer_not_an_error():
    """Ollama not running: no exception reaches the API, no claim, an honest message."""

    class DownModel:
        """Every call fails like an unreachable Ollama."""

        def bind_tools(self, tools):
            """Same model."""
            del tools
            return self

        def with_structured_output(self, schema):
            """Same model."""
            del schema
            return self

        def invoke(self, prompt):
            """Connection refused."""
            raise ConnectionError(f"Failed to connect to Ollama ({len(prompt)} messages)")

    agent, _ = make_agent()
    agent.small, agent.large = DownModel(), DownModel()
    answer = agent.ask("Quelles actions sont exposées au Brent ?")
    assert not answer.refused and answer.claims == [] and "could not write a reliable answer" in answer.answer


def test_router_failure_does_not_block_the_question():
    """No usable router output: the question is treated as in scope."""
    agent, _ = make_agent(small_script=[AIMessage(content="not json")], large_script=[AIMessage(content=""), WRITTEN])
    answer = agent.ask("Quelles actions sont exposées au Brent ?")
    assert not answer.refused and answer.claims


class DropAllVerifier:
    """AnswerVerifier stand-in that removes every claim."""

    def verify(self, answer):
        """Move every claim to removed_claims."""
        return answer.model_copy(update={"claims": [], "removed_claims": answer.claims})


def test_verifier_runs_when_available():
    """The verifier's output is what the user gets."""
    agent, _ = make_agent(large_script=[AIMessage(content=""), WRITTEN])
    agent.verifier = DropAllVerifier()
    answer = agent.ask("Quelles actions sont exposées au Brent ?")
    assert not answer.claims and answer.removed_claims


# --- pieces --------------------------------------------------------------------------------------


def test_ambiguity_rule():
    """Only an exact match (legal suffix aside, or the ticker) avoids the question; a prefix does not.

    'Total' must not silently become Total Gabon: the user most likely means TotalEnergies.
    """
    candidates = [{"name": "TotalEnergies SE", "ticker": "TTE.PA"}, {"name": "Total Gabon", "ticker": "EC.PA"}]
    assert not ambiguous("TotalEnergies", candidates, ("name", "ticker"))
    assert not ambiguous("TTE.PA", candidates, ("name", "ticker"))
    assert ambiguous("Total", candidates, ("name", "ticker"))
    assert not ambiguous("anything", candidates[:1], ("name", "ticker"))
    banks = [{"name": "Banque Orsay"}, {"name": "Banque Lumiere"}]
    assert not ambiguous("banque orsay", banks, ("name",)) and ambiguous("Banque", banks, ("name",))


def test_build_tools_names():
    """The 12 tools of the issue, in order."""
    names = [tool.name for tool in build_tools(FakeClient())]
    assert names == [
        "resolve_company",
        "resolve_factor",
        "company_exposures",
        "most_exposed",
        "simulate_shock",
        "portfolio_xray",
        "explain_path",
        "clusters",
        "similar_companies",
        "predicted_exposures",
        "hypotheses",
        "cypher_fallback",
    ]


def test_get_chat_model():
    """fake -> scripted model; ollama -> ChatOllama with the configured model; anything else -> error."""
    assert isinstance(get_chat_model(Settings(llm_provider="fake")), ScriptedChatModel)
    large = get_chat_model(Settings(llm_provider="ollama", ollama_model_large="qwen2.5:7b"), "large")
    small = get_chat_model(Settings(llm_provider="ollama", ollama_model_small="qwen2.5:3b"), "small")
    assert isinstance(large, ChatOllama) and large.model == "qwen2.5:7b" and small.model == "qwen2.5:3b"
    assert large.temperature == 0
    with pytest.raises(ValueError):
        get_chat_model(Settings(llm_provider="openai"))


def test_scripted_model_refuses_a_wrong_script():
    """A dict where a chat answer is expected is a broken test script, not a silent pass."""
    model = ScriptedChatModel(script=[{"answer": "x"}])
    with pytest.raises(ValueError):
        model.invoke("hello")


def test_ask_endpoint_with_the_fake_model(api_fake, auth_headers):
    """POST /api/ask works end to end in CI (fake model, empty script): a safe answer, never a 500."""
    response = api_fake.post(
        "/api/ask", json={"question": "Quelles actions sont exposées au Brent ?"}, headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is False and body["claims"] == [] and body["disclaimer"]
    assert api_fake.post("/api/ask", json={"question": "Dois-je acheter Petrolia ?"}, headers=auth_headers).json()[
        "refused"
    ]


def test_compact_shock_puts_amounts_before_direction_only_lines():
    """Direction-only lines are counted, not listed first: the model must see the estimated amounts."""
    lines = [ImpactLine(isin=f"ZZTEST{index:06d}", name=f"Producer {index}", truth="fact") for index in range(50)]
    lines += [
        ImpactLine(isin="ZZTEST900001", name="Small", truth="estimate", impact_pct=1.5),
        ImpactLine(isin="ZZTEST900002", name="Large", truth="estimate", impact_pct=-9.0),
        ImpactLine(isin="ZZTEST900003", name="Held", truth="estimate", impact_pct=7.7, impact_eur=77.0),
    ]
    compact = compact_shock(ShockResult(direct=lines, portfolio_impact_eur=77.0, portfolio_impact_pct=1.1))

    assert compact["portfolio_impact_eur"] == 77.0
    assert [line["name"] for line in compact["portfolio_lines"]] == ["Held"]
    assert [line["name"] for line in compact["largest_estimated_moves"]] == ["Large", "Small"]
    assert compact["direction_only_count"] == 50 and len(compact["direction_only_examples"]) == 3
    assert compact["portfolio_lines"][0]["ref"] == "Company:ZZTEST900003"


def test_jsonable_adds_refs_and_truth_to_company_rows():
    """Rows with an isin get their ref; a truth is added only where the row has none."""
    rows = jsonable([{"isin": "FR0000120271", "name": "TotalEnergies"}, {"isin": "X", "truth": "fact"}], "estimate")

    assert rows[0] == {
        "isin": "FR0000120271",
        "name": "TotalEnergies",
        "ref": "Company:FR0000120271",
        "truth": "estimate",
    }
    assert rows[1]["truth"] == "fact"
