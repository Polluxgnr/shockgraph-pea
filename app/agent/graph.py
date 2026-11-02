"""LangGraph agent (role C): router, planner, tools, writer, verifier.

    START -> router --(advice / write request)--> refuse -> END
               |                                     ^
               v                                     | (no tool fits: out of scope)
            planner <--> tools --(ambiguous name)--> clarify -> END
               |           |
               v           | (6 tool calls used)
             writer <------+
               |
               v
            verifier -> END

The advice and write checks are rules, not model calls: no prompt can talk the agent into a recommendation or a
write. Scope is decided by the planner itself: a question for which it calls no tool at its first step is out of
scope. (A separate small-model router refused 13 of 20 ordinary questions in the evaluation, see
docs/decisions/c.md.) The model only chooses tools and their arguments; every number comes from a service, and the
trace keeps each call.
"""

import json
import re
import time
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agent.prompts import PLANNER_PROMPT, WRITER_PROMPT
from app.agent.tools import RESOLVE_TOOLS, Services, build_tools_for
from app.agent.verifier import AnswerVerifier
from app.config import get_settings
from app.database.neo4j import Neo4jClient
from app.llm.client import get_chat_model
from app.models.agent import AgentAnswer, AgentClaim, ToolCall

MAX_TOOL_CALLS = 6
PREVIEW_CHARS = 300
RESULT_CHARS = 4_000  # what one tool result may take in the model's context

# Requests for a recommendation, in English and French. Checked before any model sees the question.
ADVICE_PATTERNS = [
    r"\b(should|shall|must|do|would) (i|we|you)\b.{0,40}\b(buy|sell|invest|hold|keep|short|dump)\b",
    r"\b(is it|is now) (a )?(good|bad|right|the right) (time|moment|idea)\b",
    r"\b(buy|sell) or (sell|hold|buy|keep)\b",
    r"\b(recommend|recommendation|price target)\b",
    r"\b(dois|devrais|faut)[- ](je|il|on)\b.{0,40}\b(acheter|vendre|investir|garder|conserver|placer)\b",
    r"\bje (dois|devrais)\b.{0,40}\b(acheter|vendre|investir|garder)\b",
    r"\b(acheter|vendre) ou (vendre|garder|acheter|conserver)\b",
    r"\b(conseil|conseilles|conseillez|recommandes|recommandez|recommandation)\b",
    # Direct orders and "tell me" forms (found in review of #31): "Buy Petrolia today.", "Tell me to sell...".
    r"(^|[.!?;:]\s*)(please\s+)?(buy|sell|short|dump|get rid of)\b(?![-\s]off)",
    r"\b(tell|advise|show) me\b.{0,20}\b(to|whether to|if i should) (buy|sell|invest|hold|keep|short|dump)\b",
    r"\b(what|which)\b.{0,30}\bto (buy|sell|invest in|short|dump)\b",
    r"\bbest\b.{0,20}\bto (buy|sell|invest in)\b",
    r"(^|[.!?;:]\s*)(ach[eè]te|achetez|vends|vendez|investis|investissez|liquide|liquidez)\b",
    r"\b(dis|dites)[- ]moi\b.{0,40}\b(acheter|vendre|investir|garder|liquider)\b",
    r"\b(quoi|quel|quelle|quels|quelles)\b.{0,30}\b(acheter|vendre)\b",
]
ADVICE = re.compile("|".join(ADVICE_PATTERNS), re.IGNORECASE)
# Requests to change the graph: an order to create, change or delete, with something of the database as its object
# ("Delete the rejected Vela claim."). Questions about what changed ("Which companies were added?") are not orders.
WRITE_REQUEST = re.compile(
    r"(^|[.!?;:]\s*)((please|can you|could you|peux-tu|pouvez-vous)\s+)?"
    r"(create|add|insert|delete|remove|drop|update|modify|rename|merge|write|cr[ée]e[rz]?|ajoute[rz]?|"
    r"supprime[rz]?|efface[rz]?|modifie[rz]?|renomme[rz]?)\b"
    r".{0,40}\b(nodes?|relationships?|edges?|claims?|labels?|propert(y|ies)|database|graph|neo4j|indexe?s?|"
    r"constraints?|records?|n(oe|œ)uds?|relations?|liens?|base|graphe|propri[ée]t[ée]s?)\b",
    re.IGNORECASE,
)
FRENCH = re.compile(r"\b(le|la|les|des|est|quel|quelle|quelles|quels|si|mon|ma|mes|je|dois|faut|qui|sont)\b", re.I)


def is_advice_request(question: str) -> bool:
    """True when the question asks what to buy, sell or hold."""
    return ADVICE.search(question) is not None


def is_write_request(question: str) -> bool:
    """True when the question orders a change to the graph, which is read-only here."""
    return WRITE_REQUEST.search(question) is not None


def is_french(question: str) -> bool:
    """Rough guess, enough to answer a refusal in the user's language: accents or French small words."""
    return bool(re.search(r"[àâçéèêëîïôûùœ]", question, re.I)) or len(FRENCH.findall(question)) >= 2


class WrittenAnswer(BaseModel):
    """Writer output (large model): the answer split into claims with their truth and evidence."""

    answer: str
    # required, not defaulted: with an optional field qwen2.5 left the claims out of every answer
    claims: list[AgentClaim]


class AgentState(TypedDict, total=False):
    """What flows between the nodes."""

    question: str
    route: str
    reason: str
    messages: list[BaseMessage]
    trace: list[ToolCall]
    candidates: list[dict]
    answer: AgentAnswer


class ShockAgent:
    """Answers questions about exposures and shocks using graph tools only.

    Nodes: router (an advice or write request leads to a refusal with a reason), planner (large model with
    bound tools, at most MAX_TOOL_CALLS calls; no tool at its first step means out of scope, a refusal), tools
    (an ambiguous name leads to a clarification question), writer (structured `AgentAnswer`), verifier
    (app/agent/verifier.py).
    """

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client
        settings = get_settings()
        self.services = Services.for_client(client)
        self.large = get_chat_model(settings, "large")
        self.verifier = AnswerVerifier(client)

    def ask(self, question: str) -> AgentAnswer:
        """Run the graph on one question and return the verified answer with its trace."""
        tools = {tool.name: tool for tool in build_tools_for(self.services)}
        graph = self.build_graph(tools)
        state = graph.invoke({"question": question, "messages": [], "trace": []}, {"recursion_limit": 40})
        return state["answer"]

    def build_graph(self, tools: dict):
        """Wire the nodes (see the module docstring)."""
        graph = StateGraph(AgentState)
        graph.add_node("router", self.route)
        graph.add_node("refuse", self.refuse)
        graph.add_node("planner", lambda state: self.plan(state, tools))
        graph.add_node("tools", lambda state: self.run_tools(state, tools))
        graph.add_node("clarify", self.clarify)
        graph.add_node("writer", self.write)
        graph.add_node("verifier", self.verify)
        graph.add_edge(START, "router")
        graph.add_conditional_edges("router", lambda state: "planner" if state["route"] == "answer" else "refuse")
        graph.add_edge("refuse", END)
        graph.add_conditional_edges("planner", self.after_plan)
        graph.add_conditional_edges("tools", self.after_tools)
        graph.add_edge("clarify", END)
        graph.add_edge("writer", "verifier")
        graph.add_edge("verifier", END)
        return graph.compile()

    # --- nodes -----------------------------------------------------------------------------------------

    def route(self, state: AgentState) -> dict:
        """Advice and write requests by rule, before any model; scope is left to the planner (see `plan`)."""
        question = state["question"]
        if is_advice_request(question):
            return {"route": "advice", "reason": "investment advice"}
        if is_write_request(question):
            return {"route": "write", "reason": "write request: the graph is read-only"}
        return {"route": "answer", "reason": ""}

    def refuse(self, state: AgentState) -> dict:
        """A refusal that says why and what the tool can do instead."""
        french = is_french(state["question"])
        if state["route"] == "advice":
            text = (
                "Je ne peux pas dire s'il faut acheter, vendre ou garder un titre : cet outil explique des "
                "expositions, il ne donne pas de conseil en investissement. Je peux montrer à quoi une action est "
                "exposée, ou simuler un choc (par exemple « et si le Brent prenait 20 % ? »)."
                if french
                else "I can't tell you whether to buy, sell or hold anything: this tool explains exposures, it gives "
                "no investment advice. I can show what a stock is exposed to, or simulate a shock "
                '(for example "what if Brent rises 20 %?").'
            )
        elif state["route"] == "write":
            text = (
                "Le graphe est en lecture seule ici : je ne peux rien créer, modifier ni supprimer. Je peux montrer "
                "ce qu'il contient, par exemple à quoi une action est exposée."
                if french
                else "The graph is read-only here: I can't create, change or delete anything. I can show what is in "
                "it, for example what a stock is exposed to."
            )
        else:
            text = (
                "Cette question est hors du périmètre de ShockGraph PEA (expositions des actions et ETF du PEA aux "
                "matières premières, devises et taux, et chocs sur ces facteurs)."
                if french
                else "This question is out of scope for ShockGraph PEA (exposures of PEA stocks and ETFs to "
                "commodities, currencies and rates, and shocks on those factors)."
            )
        return {"answer": AgentAnswer(answer=text, refused=True, refusal_reason=state["reason"] or state["route"])}

    def plan(self, state: AgentState, tools: dict) -> dict:
        """The large model reads the question and the tool results so far, and asks for the next tools."""
        messages = state["messages"] or [SystemMessage(PLANNER_PROMPT), HumanMessage(state["question"])]
        try:
            response = self.large.bind_tools(list(tools.values())).invoke(messages)
        except Exception:  # pylint: disable=broad-exception-caught
            # Ollama down or a malformed tool call: stop planning; the writer then gives its safe answer.
            return {"messages": [*messages, AIMessage(content="")]}
        if not state["trace"] and not response.tool_calls and str(response.content).strip():
            # The model replied but no tool fits the question: out of scope (weather, health...). Measured on the
            # 30 evaluation questions: no tool exactly on the 2 off-topic ones, a tool on every other one. An empty
            # reply is a failure, not a verdict: the writer then gives its safe answer.
            return {"messages": [*messages, response], "route": "out_of_scope", "reason": "no tool fits the question"}
        return {"messages": [*messages, response]}

    @staticmethod
    def after_plan(state: AgentState) -> str:
        """Out of scope: refuse. More tools while the model asks for some and the budget allows; then the writer."""
        if state["route"] == "out_of_scope":
            return "refuse"
        last = state["messages"][-1]
        wants_tools = isinstance(last, AIMessage) and bool(last.tool_calls)
        return "tools" if wants_tools and len(state["trace"]) < MAX_TOOL_CALLS else "writer"

    @staticmethod
    def after_tools(state: AgentState) -> str:
        """Ambiguous name: ask. Budget used up: write with what we have (no planning call for nothing)."""
        if state.get("candidates"):
            return "clarify"
        return "writer" if len(state["trace"]) >= MAX_TOOL_CALLS else "planner"

    def run_tools(self, state: AgentState, tools: dict) -> dict:
        """Run the requested tools within the budget; every call (or refusal to call) goes to the trace."""
        trace, messages = list(state["trace"]), list(state["messages"])
        candidates: list[dict] = []
        for request in messages[-1].tool_calls:
            call = self._call_tool(tools, request, budget_left=len(trace) < MAX_TOOL_CALLS)
            trace.append(call)
            content = json.dumps(call.result if call.error is None else {"error": call.error}, ensure_ascii=False)
            messages.append(ToolMessage(content=content[:RESULT_CHARS], tool_call_id=request["id"]))
            if request["name"] in RESOLVE_TOOLS and isinstance(call.result, dict) and call.result.get("ambiguous"):
                candidates = call.result["candidates"]
                break
        return {"trace": trace, "messages": messages, "candidates": candidates}

    @staticmethod
    def _call_tool(tools: dict, request: dict, budget_left: bool) -> ToolCall:
        """One tool call, timed; errors are reported to the model instead of crashing the run."""
        name, args = request["name"], request.get("args") or {}
        if not budget_left:
            return ToolCall(tool=name, args=args, error=f"skipped: limit of {MAX_TOOL_CALLS} tool calls reached")
        if name not in tools:
            return ToolCall(tool=name, args=args, error=f"unknown tool {name}")
        start = time.perf_counter()
        try:
            result = tools[name].invoke(args)
            error = None
        except NotImplementedError as exc:
            result, error = None, f"not implemented yet ({exc})"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result, error = None, f"{type(exc).__name__}: {exc}"
        preview = json.dumps(result, ensure_ascii=False)[:PREVIEW_CHARS] if error is None else ""
        duration = int((time.perf_counter() - start) * 1000)
        return ToolCall(tool=name, args=args, result=result, result_preview=preview, duration_ms=duration, error=error)

    @staticmethod
    def clarify(state: AgentState) -> dict:
        """Ask which entity the user means instead of guessing."""
        options = [_label(candidate) for candidate in state["candidates"]]
        if is_french(state["question"]):
            text = "Plusieurs résultats correspondent. Lequel voulez-vous dire : " + " ou ".join(options) + " ?"
        else:
            text = "Several matches. Which one do you mean: " + " or ".join(options) + "?"
        return {"answer": AgentAnswer(answer=text, trace=state["trace"])}

    def write(self, state: AgentState) -> dict:
        """Structured answer from the tool results; a safe answer when the model's output is unusable."""
        results = [
            {"tool": call.tool, "args": call.args, "result": call.result, "error": call.error}
            for call in state["trace"]
        ]
        prompt = [
            SystemMessage(WRITER_PROMPT),
            HumanMessage(
                f"Question: {state['question']}\n\nTool results:\n"
                + json.dumps(results, ensure_ascii=False)[: RESULT_CHARS * 2]
            ),
        ]
        try:
            written = self.large.with_structured_output(WrittenAnswer).invoke(prompt)
            answer = AgentAnswer(answer=written.answer, claims=written.claims, trace=state["trace"])
        except Exception:  # pylint: disable=broad-exception-caught
            used = ", ".join(call.tool for call in state["trace"] if call.error is None) or "none"
            answer = AgentAnswer(
                answer=f"I could not write a reliable answer from the tool results (tools used: {used}).",
                trace=state["trace"],
            )
        return {"answer": answer}

    def verify(self, state: AgentState) -> dict:
        """Remove every claim the trace does not support (C5)."""
        try:
            return {"answer": self.verifier.verify(state["answer"])}
        except NotImplementedError:
            return {"answer": state["answer"]}  # until the verifier (C5) is merged


def _label(candidate: dict[str, Any]) -> str:
    """'Banque Orsay (FRTEST000052)' or 'Brent crude oil (BRENT)'."""
    key = candidate.get("isin") or candidate.get("code") or ""
    return f"{candidate.get('name', key)} ({key})" if key else str(candidate.get("name", "?"))
