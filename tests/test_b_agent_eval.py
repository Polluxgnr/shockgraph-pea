"""Tests for the role B agent-evaluation harness."""

from app.models.agent import AgentAnswer, AgentClaim, ToolCall
from scripts.agent_eval import CASES, ScriptedAgent, evaluate


class NoisyAgent:
    """Agent double exposing removed claims and tool failures."""

    def ask(self, question: str) -> AgentAnswer:
        """Return the expected text with deliberately noisy trace metadata."""
        case = next(item for item in CASES if item.question == question)
        claim = AgentClaim(text="unsupported", truth="hypothesis")
        return AgentAnswer(
            answer=case.expected[-1],
            refused=case.refusal,
            refusal_reason="guardrail" if case.refusal else None,
            removed_claims=[claim],
            trace=[ToolCall(tool="fixture", error="rejected")],
        )


def test_eval_suite_has_required_size_and_traps() -> None:
    """The published evaluation contract contains 30 cases and ten traps."""
    assert len(CASES) == 30
    assert len({case.identifier for case in CASES}) == 30
    assert sum(case.trap for case in CASES) == 10
    assert all(case.refusal for case in CASES if case.trap)


def test_scripted_harness_validates_scoring_contract() -> None:
    """The deterministic double validates all expected-answer comparisons."""
    report = evaluate(ScriptedAgent())

    assert report["passed"] == 30
    assert report["accuracy"] == 1.0
    assert report["correct_refusals"] == 10
    assert report["removed_claims"] == 0
    assert report["tool_errors"] == 0


def test_eval_aggregates_removed_claims_and_tool_errors() -> None:
    """Aggregate observability metrics include every evaluated interaction."""
    report = evaluate(NoisyAgent())

    assert report["passed"] == 30
    assert report["removed_claims"] == 30
    assert report["tool_errors"] == 30
