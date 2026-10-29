"""Answer verifier (role C): nothing reaches the user that the trace does not support.

The writer (a language model) turns tool results into claims. This module checks each claim against the
trace and the database, with rules and no model:

1. every evidence ref (`Company:FR...`, `Factor:BRENT`, `Claim:c1`) exists in the database;
2. every number of the claim appears in the trace, in the right place (rounding allowed: "0.62" matches 0.6213,
   "48 %" matches 0.48). A number from a result row that names companies (`isin`, `company_isin`...) only
   supports a claim citing one of those companies: Petrolia's beta never backs a claim about AeroLys. A row
   without a company must share a code with the evidence (`factor_code`, `claim_id`, or the call's arguments).
   The arguments themselves (the scenario: "+20 %") and the length of a returned list support any claim;
3. the truth level is not stronger than its source: a number takes the level of the result it comes from
   (a beta is an `estimate`), a news Claim is at most a `hypothesis`. A claim stated too strongly is lowered;
4. an estimate, a prediction or a hypothesis never uses causal wording ("because", "à cause de"): those
   numbers describe co-movement, not causes;
5. no investment advice, whatever the level. Two layers: fixed phrases (instant, always on), then the agent's
   local model (qwen2.5:7b) that reads each remaining claim against a written policy, the legal definition of advice
   (MiFID II / AMF: a recommendation to buy, sell, hold... a specific instrument, or presented as suitable for
   the reader). A list of phrases can never be complete; the judge catches the indirect forms ("a good fit for
   your PEA"). The judge can only remove a claim, never add or change one, and it sees one sentence, not the
   conversation. Without a model (tests, CI, Ollama down) the phrases still apply.

A claim must also be about something the trace returned: one result object (with what its call was asked)
names every key of its evidence. "AeroLys produces Brent" with nothing about AeroLys and Brent in the trace goes.

A claim that breaks a rule moves to `removed_claims` with its reason. The prose answer is always rebuilt from
the kept claims: free prose cannot be tied to evidence, so "AeroLys beta 0.62" could hide in it. An answer
that is not a refusal but has no claim at all has nothing verified behind it: it becomes NOTHING_VERIFIED.
(The clarification question "which one do you mean?" ends the graph before the verifier.)
"""

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings, get_settings
from app.database.neo4j import Neo4jClient
from app.llm.client import get_chat_model
from app.models.agent import AgentAnswer, AgentClaim

# Strongest first: a claim may be stated as weakly as its sources, never more strongly.
TRUTH_STRENGTH = {"fact": 3, "estimate": 2, "prediction": 1, "hypothesis": 0}

# Results without a `truth` field (portfolio lines, news claims...): the field says where the number comes from.
IMPLIED_TRUTH = {
    "beta": "estimate",
    "weighted_beta": "estimate",
    "impact_pct": "estimate",
    "rho": "estimate",
    "prob": "prediction",
    "claim_id": "hypothesis",
    "confidence": "hypothesis",
}

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

# A number not glued to letters or digits: "EUR10Y" and "FR0000120271" are codes, not numbers. Groups of three
# digits after a comma or a (narrow) no-break space are thousands: "1,000", "2 470".
NUMBER = re.compile(r"(?<![\w.,])([+\-−]?)(\d{1,3}(?:[,\u00a0\u202f]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)(?![\w])(\s*%)?")
THOUSANDS = re.compile(r"\d{1,3}(?:[,\u00a0\u202f]\d{3})+(?:\.\d+)?")

CAUSAL = re.compile(
    r"\b(because|caused|causes|due to|leads? to|drives|driven by|results? in|as a result of|thanks to|owing to|"
    r"parce que|[àa] cause d(?:e|u|es|')|provoqu\w*|entra[iî]n\w*|d[uû]e?s? (?:à|au|aux)|gr[âa]ce (?:à|au|aux)|"
    r"en raison d(?:e|u|es|')|suite (?:à|au|aux)|sous l'effet d(?:e|u|es|')|du fait d(?:e|u|es|'))",
    re.IGNORECASE,
)
# A direct order, or a modal / recommendation word followed closely by buy or sell (English and French).
ADVICE = re.compile(
    r"(?:^|[.!?;:]\s+)(buy|sell)\b|"
    r"\b(achetez|vendez|renforcez|all[ée]gez)\b|"
    r"\b(should|could|might|consider\w*|worth|time to|idea to|recommend\w*|advis\w*|suggest\w*)\b[^.!?]{0,40}?"
    r"\b(buy|sell|buying|selling|purchas\w*)\b|"
    r"\b(devr\w*|dev(?:ez|ons)|conseill\w*|recommand\w*|sugg[eè]r\w*|faut|int[ée]ressant|moment)\b[^.!?]{0,40}?"
    r"\b(acheter|vendre|achat|vente|renforcer|all[ée]ger|c[ée]der)\b",
    re.IGNORECASE,
)

NOTHING_VERIFIED = "I could not verify any statement against the data the tools returned."

# The policy of the advice judge: the legal definition of investment advice (MiFID II art. 4(4), delegated
# regulation 2017/565 art. 9, as the AMF applies it), written for a small local model. The examples here are not in the
# labelled set of ml/advice_eval.py, so the measure there is not inflated.
ADVICE_POLICY = """You check ONE sentence written by ShockGraph PEA, a tool that explains how stocks and ETFs \
are exposed to commodities, currencies and interest rates. The tool must never give investment advice.

ADVICE: the sentence recommends, suggests or encourages the reader to buy, sell, hold, keep, subscribe to, \
increase, reduce, switch or avoid a specific stock, fund or asset; or says one is a good or bad choice, an \
opportunity, or suitable for the reader; or proposes how to split a portfolio. Polite or indirect forms count \
("it may be wise to", "worth a look", "il serait judicieux de", "à privilégier").

SAFE: the sentence only describes exposures, betas, correlations, simulated impacts, predictions, what a company \
produces, buys or sells, what a fund holds, or past moves, without telling the reader what to do. Saying that \
the tool gives no advice is SAFE.

Examples:
"Shell has a beta of 0.7 to Brent." -> SAFE
"Shell looks like a smart pick for your account." -> ADVICE
"Volkswagen consomme du cuivre." -> SAFE
"Mieux vaut sortir de Volkswagen avant la hausse du cuivre." -> ADVICE

Answer with exactly one word: ADVICE or SAFE."""
JUDGE_REASON = "the advice judge read it as a recommendation to buy, sell or hold: the app never advises"

# Fields that name the object a row is about. Any field ending in "isin" names a company or a fund.
CODE_FIELDS = ("code", "factor_code", "claim_id")


@dataclass(frozen=True)
class Number:
    """A number as written in a claim: its sign ('' when none), its digits, and whether a % follows."""

    sign: str
    digits: str
    percent: bool

    @property
    def value(self) -> float:
        """'−', '0,62' -> -0.62 (French decimal comma, Unicode minus); '1,000' -> 1000."""
        return self.readings()[0][0]

    def readings(self) -> list[tuple[float, int]]:
        """(value, decimals shown) for each way to read the digits: '1,000' is 1000, or 1.000 with a French comma."""
        readings = []
        if THOUSANDS.fullmatch(self.digits):
            plain = re.sub(r"[,\u00a0\u202f]", "", self.digits)
            readings.append((_to_float(self.sign, plain), len(plain.partition(".")[2])))
        if re.fullmatch(r"\d+(?:[.,]\d+)?", self.digits):
            readings.append((_to_float(self.sign, self.digits), len(self.digits.replace(",", ".").partition(".")[2])))
        return readings

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
    for written, decimals in number.readings():
        tolerance = 0.5 * 10**-decimals + 1e-9
        for value in values:
            for candidate in (value, value * 100) if number.percent else (value,):
                if number.sign and candidate and math.copysign(1, candidate) != math.copysign(1, written):
                    continue
                if abs(abs(candidate) - abs(written)) <= tolerance:
                    return True
    return False


@dataclass(frozen=True)
class Source:
    """The numbers one result object holds itself, and what it is about.

    `isins`: the companies it names, its own or its nearest parent's. `codes`: the factor codes and claim ids of
    the object and its parents, plus every key of the call's arguments.
    """

    numbers: tuple[float, ...]
    isins: frozenset[str]
    codes: frozenset[str]
    texts: frozenset[str] = frozenset()

    def names_all(self, keys: set[str]) -> bool:
        """True when the object, its parents or its call's arguments name every key."""
        return keys <= self.isins | self.codes | self.texts

    def supports(self, number: "Number", keys: set[str]) -> bool:
        """True when the object holds the number and is about one of the claim's evidence keys."""
        if not matches(number, list(self.numbers)):
            return False
        if self.isins:
            return bool(self.isins & keys)
        return not self.codes or bool(self.codes & keys)


@dataclass
class TraceFacts:
    """What the trace supports: every number it contains, and every result object (dict) it returned.

    `numbers` checks the prose; `free` (arguments, list lengths) and `sources` (result objects) check a claim.
    """

    numbers: list[float] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    free: list[float] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)

    @classmethod
    def from_answer(cls, answer: AgentAnswer) -> "TraceFacts":
        """Collect from every successful tool call: its arguments and its full result."""
        facts = cls()
        for call in answer.trace:
            if not call.error:
                result = _full_result(call)
                facts.collect(call.args)
                facts.collect(result)
                facts.free += _free_numbers(call.args)
                facts.bind(result, frozenset(), frozenset(_keys_in(call.args)))
        return facts

    def bind(self, value: Any, isins: frozenset[str], codes: frozenset[str]) -> None:
        """Walk a result: each object becomes a Source, inheriting what its parents are about."""
        if isinstance(value, dict):
            own_isins, own_codes = _identity(value)
            isins = frozenset(own_isins) or isins
            codes = codes | own_codes
            self.sources.append(Source(tuple(_held_numbers(value)), isins, codes, frozenset(_texts(value))))
            for item in value.values():
                if isinstance(item, (dict, list, tuple)):
                    self.bind(item, isins, codes)
        elif isinstance(value, (list, tuple)):
            self.free.append(float(len(value)))
            for item in value:
                self.bind(item, isins, codes)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self.free.append(float(value))  # a bare number, outside any object

    def about(self, keys: set[str]) -> bool:
        """True when one result object of the trace is about every key of the evidence."""
        return any(source.names_all(keys) for source in self.sources)

    def unsupported_for(self, text: str, keys: set[str]) -> list[Number]:
        """Numbers of a claim that neither the arguments nor a result object about its evidence support."""
        return [
            number
            for number in numbers_in(text)
            if not matches(number, self.free) and not any(source.supports(number, keys) for source in self.sources)
        ]

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


def _identity(item: dict) -> tuple[set[str], set[str]]:
    """(companies, codes) an object names directly."""
    isins = {value for key, value in item.items() if key.endswith("isin") and isinstance(value, str)}
    codes = {item[key] for key in CODE_FIELDS if isinstance(item.get(key), str)}
    return isins, codes


def _held_numbers(item: dict) -> list[float]:
    """Numbers an object holds itself: its numeric fields and the numbers written in its text fields."""
    held = _own_numbers(item)
    for value in item.values():
        if isinstance(value, str):
            held += [number.value for number in numbers_in(value)]
    return held


def _free_numbers(value: Any) -> list[float]:
    """Every number of the arguments: the scenario the user asked for supports any claim."""
    facts = TraceFacts()
    facts.collect(value)
    return facts.numbers


def _keys_in(value: Any) -> set[str]:
    """Every text value of the arguments (ISINs, factor codes): what the call was about."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_keys_in(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(_keys_in(item) for item in value))
    return set()


def _texts(item: dict) -> set[str]:
    """The plain values of an object, as text (where an ISIN or a factor code would appear)."""
    return {str(value) for value in item.values() if isinstance(value, (str, int)) and not isinstance(value, bool)}


def _own_numbers(item: dict) -> list[float]:
    """Numbers held directly by an object (not by the objects inside it)."""
    return [float(value) for value in item.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]


def _levels(item: dict) -> list[str]:
    """Truth levels an object states: `truth`, or `truth_levels` on a path; else what its fields imply."""
    found = [item["truth"]] if item.get("truth") in TRUTH_STRENGTH else []
    found += [level for level in item.get("truth_levels") or [] if level in TRUTH_STRENGTH]
    return found or sorted({level for key, level in IMPLIED_TRUTH.items() if item.get(key) is not None})


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
        self.judge = AdviceJudge.from_settings(get_settings())

    def verify(self, answer: AgentAnswer) -> AgentAnswer:
        """Return a copy of the answer with unsupported claims removed."""
        if answer.refused:
            return answer
        if not answer.claims:
            return answer.model_copy(update={"answer": NOTHING_VERIFIED})
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
        if kept and self.judge:
            verdicts = self.judge.advice([claim.text for claim in kept])
            removed += [claim.model_copy(update={"reason": JUDGE_REASON}) for claim, bad in zip(kept, verdicts) if bad]
            kept = [claim for claim, bad in zip(kept, verdicts) if not bad]
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


class AdviceJudge:
    """The agent's large model (qwen2.5:7b) tells whether a sentence is investment advice (ADVICE_POLICY).

    Large, not small: on claims in the writer's own format, qwen2.5:3b flagged 2 of 10 plain impacts as advice
    ("TotalEnergies would move +10.92 %"), the 7B none; the 7B is also already loaded for the planner.

    One call per sentence, all sent together (`batch`). A verdict is True (advice), False (safe) or None when
    the model did not answer ADVICE or SAFE or could not be reached: None leaves the decision to the phrases.
    """

    def __init__(self, model) -> None:
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "AdviceJudge | None":
        """The judge on the large model; None when there is no chat model yet (C4 not merged)."""
        try:
            return cls(get_chat_model(settings, "large"))
        except NotImplementedError:
            return None

    def advice(self, texts: list[str]) -> list[bool | None]:
        """One verdict per sentence."""
        prompts = [[SystemMessage(ADVICE_POLICY), HumanMessage(f"Sentence: {text}")] for text in texts]
        try:
            replies = self.model.batch(prompts)
        except Exception:  # pylint: disable=broad-exception-caught
            return [None] * len(texts)  # Ollama down or broken: the phrases still apply
        return [read_verdict(reply.content) for reply in replies]


def read_verdict(reply: Any) -> bool | None:
    """'ADVICE' -> True, 'SAFE' -> False (first word, any case), anything else -> None."""
    words = re.findall(r"[A-Za-z]+", str(reply))
    first = words[0].upper() if words else ""
    return {"ADVICE": True, "SAFE": False}.get(first)


def check_claim(claim: AgentClaim, facts: TraceFacts, existing: set[str]) -> tuple[AgentClaim, str | None]:
    """The claim as it may be shown (truth lowered if needed), or the reason it must be removed."""
    reason = evidence_problem(claim, facts, existing)
    if reason:
        return claim, reason
    if ADVICE.search(claim.text):
        return claim, "buy or sell wording: the app explains exposures, it never advises"
    checked = lower_truth(claim, facts)
    if checked.truth != "fact" and CAUSAL.search(claim.text):
        return checked, f"causal wording on a {checked.truth}: these numbers describe co-movement, not causes"
    return checked, None


def evidence_problem(claim: AgentClaim, facts: TraceFacts, existing: set[str]) -> str | None:
    """Why the claim is not backed by its evidence (refs in the database, then the trace), or None."""
    if not claim.evidence:
        return "no evidence ref"
    missing = [ref for ref in claim.evidence if ref not in existing]
    if missing:
        return f"evidence not in the database: {', '.join(missing)}"
    keys = {split_ref(ref)[1] for ref in claim.evidence}
    if not facts.about(keys):
        return f"no tool result is about {', '.join(claim.evidence)}"
    unsupported = facts.unsupported_for(claim.text, keys)
    if unsupported:
        return f"number not found in a tool result about its evidence: {', '.join(map(str, unsupported))}"
    return None


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
