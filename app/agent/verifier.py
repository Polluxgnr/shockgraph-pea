"""Answer verifier (role C): nothing reaches the user that the trace does not support.

The writer (a language model) turns tool results into claims. This module checks each claim against the
trace and the database, with rules and no model:

1. every evidence ref (`Company:FR...`, `Factor:BRENT`, `Claim:c1`) exists in the database;
2. every number of the claim appears in a tool result or a tool argument of the trace (rounding allowed:
   "0.62" matches 0.6213, "48 %" matches 0.48);
3. the truth level is not stronger than its source: a number takes the level of the result it comes from
   (a beta is an `estimate`), a news Claim is at most a `hypothesis`. A claim stated too strongly is lowered;
4. an estimate, a prediction or a hypothesis never uses causal wording ("because", "à cause de"): those
   numbers describe co-movement, not causes;
5. no buy or sell wording, whatever the level.

A claim that breaks rule 1, 2, 4 or 5 moves to `removed_claims` with its reason. When anything was removed,
the prose answer is rebuilt from the kept claims, so a removed number cannot survive in the text.
"""

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.database.neo4j import Neo4jClient
from app.models.agent import AgentAnswer, AgentClaim

# Strongest first: a claim may be stated as weakly as its sources, never more strongly.
TRUTH_STRENGTH = {"fact": 3, "estimate": 2, "prediction": 1, "hypothesis": 0}

# One query per label, written out: the label of a ref never goes into a Cypher string.
EXISTING = {
    "Company": "MATCH (n:Company) WHERE n.isin IN $keys RETURN n.isin AS key LIMIT $limit",
    "Fund": "MATCH (n:Fund) WHERE n.isin IN $keys RETURN n.isin AS key LIMIT $limit",
    "Factor": "MATCH (n:Factor) WHERE n.code IN $keys RETURN n.code AS key LIMIT $limit",
    "Claim": "MATCH (n:Claim) WHERE n.claim_id IN $keys RETURN n.claim_id AS key LIMIT $limit",
    "Index": "MATCH (n:Index) WHERE n.code IN $keys RETURN n.code AS key LIMIT $limit",
    "Sector": "MATCH (n:Sector) WHERE n.code IN $keys RETURN n.code AS key LIMIT $limit",
    "Industry": "MATCH (n:Industry) WHERE n.code IN $keys RETURN n.code AS key LIMIT $limit",
    "Country": "MATCH (n:Country) WHERE n.code IN $keys RETURN n.code AS key LIMIT $limit",
    "Article": "MATCH (n:Article) WHERE n.url IN $keys RETURN n.url AS key LIMIT $limit",
}

# A number not glued to letters or digits: "EUR10Y" and "FR0000120271" are codes, not numbers.
NUMBER = re.compile(r"(?<![\w.,])([+\-−]?)(\d+(?:[.,]\d+)?)(?![\w])(\s*%)?")

CAUSAL = re.compile(
    r"\b(because|caused|causes|due to|leads? to|drives|driven by|results? in|"
    r"parce que|[àa] cause d(?:e|u|es|')|provoqu\w*|entra[iî]n\w*|d[uû]e?s? (?:à|au|aux)|gr[âa]ce (?:à|au|aux))",
    re.IGNORECASE,
)
ADVICE = re.compile(
    r"\b(you should (buy|sell)|(buy|sell) (it|this|these|now)|we recommend|i recommend|"
    r"achetez|vendez|il faut (acheter|vendre)|je (vous )?(conseille|recommande))",
    re.IGNORECASE,
)

NOTHING_VERIFIED = "I could not verify any statement against the data the tools returned."


@dataclass(frozen=True)
class Number:
    """A number as written in a claim: its sign ('' when none), its digits, and whether a % follows."""

    sign: str
    digits: str
    percent: bool

    @property
    def value(self) -> float:
        """'−', '0,62' -> -0.62 (French decimal comma, Unicode minus)."""
        return _to_float(self.sign, self.digits)

    def __str__(self) -> str:
        return f"{self.sign}{self.digits}{'%' if self.percent else ''}"


def numbers_in(text: str) -> list[Number]:
    """Every number written in a text."""
    return [Number(sign, digits, bool(percent)) for sign, digits, percent in NUMBER.findall(text)]


def matches(number: Number, values: list[float]) -> bool:
    """True when one of `values` rounds to the number as written.

    "0.62" matches 0.6213 (half a unit of the last digit shown). With a "%", a share (0.48) matches "48 %"
    too. Without a sign only the size is compared ("falls 8.2 %" matches -8.2); with a sign, it must agree.
    """
    decimals = len(number.digits.replace(",", ".").partition(".")[2])
    tolerance = 0.5 * 10**-decimals + 1e-9
    for value in values:
        for candidate in (value, value * 100) if number.percent else (value,):
            if number.sign and candidate and math.copysign(1, candidate) != math.copysign(1, number.value):
                continue
            if abs(abs(candidate) - abs(number.value)) <= tolerance:
                return True
    return False


@dataclass
class TraceFacts:
    """What the trace supports: every number it contains, and every result object (dict) it returned."""

    numbers: list[float] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "TraceFacts":
        """Collect from every successful tool call: its arguments and its full result."""
        facts = cls()
        for call in answer.trace:
            if not call.error:
                facts.collect(call.args)
                facts.collect(_full_result(call))
        return facts

    def collect(self, value: Any) -> None:
        """Walk a JSON-like value: numbers (and numbers inside strings) are kept, dicts are kept as sources."""
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            self.numbers.append(float(value))
        elif isinstance(value, str):
            self.numbers += [number.value for number in numbers_in(value)]
        elif isinstance(value, dict):
            self.objects.append(value)
            for item in value.values():
                self.collect(item)
        elif isinstance(value, (list, tuple)):
            self.numbers.append(float(len(value)))  # "3 companies" counts a list a tool returned
            for item in value:
                self.collect(item)

    def unsupported(self, text: str) -> list[Number]:
        """Numbers of a text that no tool result supports."""
        return [number for number in numbers_in(text) if not matches(number, self.numbers)]

    def truth_of(self, keys: set[str], numbers: list[Number]) -> str | None:
        """Truth level the sources allow for a claim about `keys` (ISINs, codes...).

        With numbers: the weakest level of the objects those numbers come from (a beta is an estimate, even
        next to a declared fact), preferring objects that mention every key, else any key (a shock line names
        the company, not the factor). Without numbers: the strongest level of the objects that mention every
        key.
        """
        related = [item for item in self.objects if keys <= _texts(item)]
        if numbers:
            holding = [item for item in self.objects if any(matches(number, _own_numbers(item)) for number in numbers)]
            sources = [item for item in holding if keys <= _texts(item)] or [
                item for item in holding if keys & _texts(item)
            ]
            levels = [level for item in sources for level in _levels(item)]
            return min(levels, key=TRUTH_STRENGTH.__getitem__) if levels else None
        levels = [level for item in related for level in _levels(item)]
        return max(levels, key=TRUTH_STRENGTH.__getitem__) if levels else None


def _texts(item: dict) -> set[str]:
    """The plain values of an object, as text (where an ISIN or a factor code would appear)."""
    return {str(value) for value in item.values() if isinstance(value, (str, int)) and not isinstance(value, bool)}


def _own_numbers(item: dict) -> list[float]:
    """Numbers held directly by an object (not by the objects inside it)."""
    return [float(value) for value in item.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]


def _levels(item: dict) -> list[str]:
    """Truth levels an object states: `truth`, or `truth_levels` on a path."""
    found = [item["truth"]] if item.get("truth") in TRUTH_STRENGTH else []
    return found + [level for level in item.get("truth_levels") or [] if level in TRUTH_STRENGTH]


def _full_result(call) -> Any:
    """The full result when the trace kept it (`ToolCall.result`), else the preview, parsed if it is JSON."""
    if call.result is not None:
        return call.result
    try:
        return json.loads(call.result_preview)
    except (TypeError, ValueError):
        return call.result_preview


def _to_float(sign: str, digits: str) -> float:
    """Signed float from a sign ('', '+', '-', '−') and digits with a dot or a comma."""
    value = float(digits.replace(",", "."))
    return -value if sign in ("-", "−") else value


def split_ref(ref: str) -> tuple[str, str] | None:
    """'Company:FR0000120271' -> ('Company', 'FR0000120271'); None when it is not a known Label:key."""
    label, _, key = ref.partition(":")
    return (label, key) if label in EXISTING and key else None


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
        if answer.refused or not answer.claims:
            return answer
        facts = TraceFacts.from_answer(answer)
        existing = self.existing_refs({ref for claim in answer.claims for ref in claim.evidence})
        kept: list[AgentClaim] = []
        removed = list(answer.removed_claims)
        for claim in answer.claims:
            checked, reason = check_claim(claim, facts, existing)
            if reason:
                removed.append(claim.model_copy(update={"reason": reason}))
            else:
                kept.append(checked)
        text = answer.answer
        if len(removed) > len(answer.removed_claims) or facts.unsupported(text):
            text = " ".join(claim.text for claim in kept) or NOTHING_VERIFIED
        return answer.model_copy(update={"answer": text, "claims": kept, "removed_claims": removed})

    def existing_refs(self, refs: set[str]) -> set[str]:
        """The refs that name a node of the database (one query per label, never the label as text)."""
        keys: dict[str, set[str]] = {}
        for ref in refs:
            parts = split_ref(ref)
            if parts:
                keys.setdefault(parts[0], set()).add(parts[1])
        found = set()
        for label, wanted in keys.items():
            rows = self.client.read(EXISTING[label], {"keys": sorted(wanted), "limit": len(wanted)})
            found |= {f"{label}:{row['key']}" for row in rows}
        return found


def check_claim(claim: AgentClaim, facts: TraceFacts, existing: set[str]) -> tuple[AgentClaim, str | None]:
    """The claim as it may be shown (truth lowered if needed), or the reason it must be removed."""
    if not claim.evidence:
        return claim, "no evidence ref"
    missing = [ref for ref in claim.evidence if ref not in existing]
    if missing:
        return claim, f"evidence not in the database: {', '.join(missing)}"
    unsupported = facts.unsupported(claim.text)
    if unsupported:
        return claim, f"number not found in any tool result: {', '.join(map(str, unsupported))}"
    if ADVICE.search(claim.text):
        return claim, "buy or sell wording: the app explains exposures, it never advises"
    checked = lower_truth(claim, facts)
    if checked.truth != "fact" and CAUSAL.search(claim.text):
        return checked, f"causal wording on a {checked.truth}: these numbers describe co-movement, not causes"
    return checked, None


def lower_truth(claim: AgentClaim, facts: TraceFacts) -> AgentClaim:
    """Lower the claim to what its sources allow; anything resting on a news Claim is at most a hypothesis."""
    keys = {split_ref(ref)[1] for ref in claim.evidence}
    levels = [level for level in (facts.truth_of(keys, numbers_in(claim.text)),) if level]
    if any(ref.startswith("Claim:") for ref in claim.evidence):
        levels.append("hypothesis")
    if not levels:
        return claim
    allowed = min(levels, key=TRUTH_STRENGTH.__getitem__)
    if TRUTH_STRENGTH[claim.truth] <= TRUTH_STRENGTH[allowed]:
        return claim
    return claim.model_copy(
        update={"truth": allowed, "reason": f"stated as {claim.truth}, its source is a {allowed}: lowered"}
    )
