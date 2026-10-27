"""LangGraph agent (role C): router, planner, tools, writer, verifier.

    START -> router --(advice / out of scope)--> refuse -> END
               |
               v
            planner <--> tools --(ambiguous name)--> clarify -> END
               |           |
               v           | (6 tool calls used)
             writer <------+
               |
               v
            verifier -> END

The advice check is a rule, not a model call: no prompt can talk the agent into a recommendation. The model
only chooses tools and their arguments; every number comes from a service, and the trace keeps each call.
"""

import json
import re
import time
from typing import Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent.prompts import PLANNER_PROMPT, ROUTER_PROMPT, WRITER_PROMPT
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
]
ADVICE = re.compile("|".join(ADVICE_PATTERNS), re.IGNORECASE)
FRENCH = re.compile(r"\b(le|la|les|des|est|quel|quelle|quelles|quels|si|mon|ma|mes|je|dois|faut|qui|sont)\b", re.I)


def is_advice_request(question: str) -> bool:
    """True when the question asks what to buy, sell or hold."""
    return ADVICE.search(question) is not None


def is_french(question: str) -> bool:
    """Rough guess, enough to answer a refusal in the user's language: accents or French small words."""
    return bool(re.search(r"[àâçéèêëîïôûùœ]", question, re.I)) or len(FRENCH.findall(question)) >= 2


class RouteDecision(BaseModel):
    """Router output (small model)."""

    route: Literal["answer", "out_of_scope"]
    reason: str = ""


class WrittenAnswer(BaseModel):
    """Writer output (large model): the answer split into claims with their truth and evidence."""

    answer: str
    claims: list[AgentClaim] = Field(default_factory=list)


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

    Nodes: router (out of scope or advice request leads to a refusal with a reason; ambiguous name
    leads to a clarification question), planner (large model with bound tools, at most MAX_TOOL_CALLS
    calls), tools, writer (structured `AgentAnswer`), verifier (app/agent/verifier.py).
    """

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client
        settings = get_settings()
        self.services = Services.for_client(client)
        self.small = get_chat_model(settings, "small")
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
        """Advice requests by rule; scope by the small model (in scope when the model fails)."""
        question = state["question"]
        if is_advice_request(question):
            return {"route": "advice", "reason": "investment advice"}
        try:
            decision = self.small.with_structured_output(RouteDecision).invoke(
                [SystemMessage(ROUTER_PROMPT), HumanMessage(question)]
            )
        except Exception:  # pylint: disable=broad-exception-caught
            # The scope check is a convenience, not a safety rule: planning is still bounded by the tools.
            return {"route": "answer", "reason": "router unavailable"}
        return {"route": decision.route, "reason": decision.reason}

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
        else:
            text = (
                "Cette question sort du périmètre de ShockGraph PEA (expositions des actions et ETF du PEA aux "
                "matières premières, devises et taux, et chocs sur ces facteurs)."
                if french
                else "This question is outside what ShockGraph PEA covers (exposures of PEA stocks and ETFs to "
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
            response = AIMessage(content="")
        return {"messages": [*messages, response]}

    @staticmethod
    def after_plan(state: AgentState) -> str:
        """More tools while the model asks for some and the budget allows; then the writer."""
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
