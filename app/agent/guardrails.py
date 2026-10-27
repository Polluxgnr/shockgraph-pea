"""Cypher guardrails (role A): the last line of defence before LLM-generated Cypher reaches Neo4j.

The validator is deliberately conservative: it accepts a small, read-only, bounded subset of Cypher over
the known schema and refuses everything else, with one reason per broken rule. String literals and
comments are blanked first, so a keyword hidden in a string is ignored and a keyword hidden behind a
comment is still seen.
"""

import re
from dataclasses import dataclass, field

ALLOWED_LABELS = frozenset({"Company", "Fund", "Index", "Sector", "Industry", "Country", "Factor", "Article", "Claim"})
SUB_LABELS = frozenset({"Commodity", "Currency", "Rate"})
ALLOWED_RELATIONSHIPS = frozenset(
    {
        "TRACKS",
        "HOLDS",
        "CONSTITUENT",
        "IN_INDUSTRY",
        "PART_OF",
        "HEADQUARTERED_IN",
        "PRODUCES",
        "CONSUMES",
        "SENSITIVE_TO",
        "CORRELATED_WITH",
        "PREDICTED_SENSITIVITY",
        "SUBJECT",
        "OBJECT",
        "EXTRACTED_FROM",
        "MENTIONS",
    }
)
MAX_LIMIT = 100
MAX_HOPS = 6

WRITE_CLAUSES = [
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    "LOAD CSV",
    "IN TRANSACTIONS",
    "ALTER",
    "RENAME",
    "GRANT",
    "REVOKE",
    "DENY",
    "TERMINATE",
    "START DATABASE",
    "STOP DATABASE",
]
FORBIDDEN_CLAUSES = ["USE", "SHOW", "PROFILE"]
ALLOWED_PROCEDURES = [
    re.compile(r"^db\.index\.fulltext\.querynodes$"),
    re.compile(r"^db\.labels$"),
    re.compile(r"^db\.relationshiptypes$"),
    re.compile(r"^gds(\.[a-z0-9]+)+\.stream$"),
]
START_CLAUSES = ("MATCH", "OPTIONAL MATCH", "WITH", "UNWIND", "CALL", "RETURN")

_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", re.S)
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_BACKTICK = re.compile(r"`([^`]*)`")
_MAP_LITERAL = re.compile(r"\{\s*[A-Za-z_]\w*\s*:[^{}]*\}")
_REL_PATTERN = re.compile(r"\[([^\[\]]*)\]")
_LABEL_USE = re.compile(r"(?:\b[A-Za-z_]\w*|\()\s*:\s*(!?[A-Za-z_]\w*(?:\s*[:&|]\s*!?[A-Za-z_]\w*)*)")
# A quantifier after a node group `)` or, since Neo4j 5.9, right after a relationship (`]-`, `]->`, `-->`).
_QUANTIFIER = re.compile(r"(?:\)|-|>)\s*(\+|\*|\{\s*(\d*)\s*,\s*(\d*)\s*\}|\{\s*(\d+)\s*\})")
_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.I)
_LIMIT = re.compile(r"\bLIMIT\b", re.I)
# An integer literal followed by the end, `}`, `;`, UNION or the next clause: `LIMIT 10 + 100000` is refused.
_STRICT_LIMIT = re.compile(
    r"\bLIMIT\s+(\d+)\s*(?=$|\}|;|\b(?:UNION|MATCH|OPTIONAL|WITH|UNWIND|CALL|RETURN|ORDER|SKIP)\b)", re.I
)
# A map right after a variable: projection `c {.name, beta: r.beta}` or properties `(c:Company {isin: $isin})`.
_MAP_AFTER_NAME = re.compile(r"\b(\w+)\s*\{[^{}]*\}")
SUBQUERY_KEYWORDS = {"CALL", "EXISTS", "COUNT", "COLLECT"}


@dataclass
class ValidationResult:
    """Verdict of the validator. `reasons` is empty when the query is valid."""

    valid: bool
    reasons: list[str] = field(default_factory=list)


def sanitize(cypher: str) -> str:
    """Blank string literals, remove comments, unquote backtick identifiers, collapse whitespace."""
    text = _STRING.sub("''", cypher)
    text = _COMMENT.sub(" ", text)
    text = _BACKTICK.sub(lambda match: re.sub(r"\W", "_", match.group(1)), text)
    return re.sub(r"\s+", " ", text).strip()


class CypherValidator:
    """Accepts only bounded, read-only queries over the known schema."""

    def validate(self, cypher: str) -> ValidationResult:
        """Check a query against every rule; the result lists all the rules it breaks."""
        text = sanitize(cypher)
        if not text:
            return ValidationResult(False, ["empty query"])
        text = text[:-1].rstrip() if text.endswith(";") else text
        reasons: list[str] = []
        if ";" in text:
            reasons.append("several statements (';') are not allowed")
        upper = text.upper()
        if not upper.startswith(START_CLAUSES):
            reasons.append("a query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, CALL or RETURN")
        reasons += self._clauses(upper)
        reasons += self._procedures(text)
        reasons += self._schema_and_paths(text)
        reasons += self._limit(upper)
        return ValidationResult(not reasons, reasons)

    @staticmethod
    def _clauses(upper: str) -> list[str]:
        """Write and administration clauses."""
        reasons = [f"write clause {clause} is not allowed" for clause in WRITE_CLAUSES if _has_word(upper, clause)]
        reasons += [f"clause {clause} is not allowed" for clause in FORBIDDEN_CLAUSES if _has_word(upper, clause)]
        if "RETURN" not in re.findall(r"\b[A-Z]+\b", upper):
            reasons.append("a read query must RETURN something")
        return reasons

    @staticmethod
    def _procedures(text: str) -> list[str]:
        """Only an allow-list of read-only procedures; no apoc or dbms at all."""
        reasons = []
        for name in _CALL.findall(text):
            if not any(pattern.match(name.lower()) for pattern in ALLOWED_PROCEDURES):
                reasons.append(f"procedure {name} is not allowed")
        lowered = text.lower()
        if re.search(r"\bapoc\.", lowered):
            reasons.append("apoc procedures and functions are not allowed")
        if re.search(r"\bdbms\.", lowered):
            reasons.append("dbms procedures are not allowed")
        if re.search(r"\bgds\.[\w.]*\b(write|mutate|drop)\b", lowered) or re.search(r"\bgds\.graph\.", lowered):
            reasons.append("gds procedures may only stream results")
        return reasons

    @staticmethod
    def _schema_and_paths(text: str) -> list[str]:
        """Labels and relationship types must exist; variable-length paths must be bounded."""
        reasons: list[str] = []
        scrubbed = text
        while _MAP_LITERAL.search(scrubbed):
            scrubbed = _MAP_LITERAL.sub("{}", scrubbed)
        scrubbed = _MAP_AFTER_NAME.sub(_blank_projection, scrubbed)
        for body in _REL_PATTERN.findall(scrubbed):
            reasons += _check_relationship(body)
        without_rels = _REL_PATTERN.sub("[]", scrubbed)
        for expression in _LABEL_USE.findall(without_rels):
            for label in re.split(r"[:&|!\s]+", expression):
                if label and label not in ALLOWED_LABELS | SUB_LABELS:
                    reasons.append(f"unknown label {label}")
        for plus_or_star, low, high, exact in _QUANTIFIER.findall(scrubbed):
            del low
            if plus_or_star in ("+", "*") or (plus_or_star.startswith("{") and "," in plus_or_star and not high):
                reasons.append("unbounded quantified path")
            elif int(high or exact or 0) > MAX_HOPS:
                reasons.append(f"quantified path longer than {MAX_HOPS} hops")
        return sorted(set(reasons), key=reasons.index)

    @staticmethod
    def _limit(upper: str) -> list[str]:
        """Every LIMIT is a single integer literal of at most MAX_LIMIT, and the final RETURN has one."""
        last_return = upper.rfind("RETURN")
        reasons = []
        if last_return < 0 or not _LIMIT.search(upper[last_return:]):
            reasons.append(f"the final RETURN needs a LIMIT (at most {MAX_LIMIT})")
        strict = {match.start(): int(match.group(1)) for match in _STRICT_LIMIT.finditer(upper)}
        for match in _LIMIT.finditer(upper):
            if match.start() not in strict:
                reasons.append("LIMIT must be a single integer literal")
            elif strict[match.start()] > MAX_LIMIT:
                reasons.append(f"LIMIT {strict[match.start()]} is above {MAX_LIMIT}")
        return reasons


def _blank_projection(match: re.Match) -> str:
    """Keep the variable, drop the map after it; keep subquery braces (CALL {...}, EXISTS {...})."""
    return match.group(0) if match.group(1).upper() in SUBQUERY_KEYWORDS else match.group(1)


def _has_word(upper: str, phrase: str) -> bool:
    """True when the phrase appears as whole words (multi-word phrases allow any whitespace)."""
    pattern = r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b"
    return re.search(pattern, upper) is not None


def _check_relationship(body: str) -> list[str]:
    """Check one `[...]` pattern such as `r:SENSITIVE_TO|CONSUMES*1..3 {}`: types and length bounds."""
    body = body.replace("{}", " ").strip()
    if ":" not in body and "*" not in body:
        return []
    head, star, tail = body.partition("*")
    reasons = []
    if ":" in head:
        for rel_type in re.split(r"[|:&!\s]+", head.split(":", 1)[1]):
            if rel_type and rel_type not in ALLOWED_RELATIONSHIPS:
                reasons.append(f"unknown relationship type {rel_type}")
    if star:
        reasons += _check_length("*" + tail.strip())
    return reasons


def _check_length(length: str) -> list[str]:
    """'*', '*..', '*2..' are unbounded; '*..n', '*m..n' and '*n' must stay within MAX_HOPS."""
    bounds = length[1:].strip()
    if not bounds or bounds.endswith(".."):
        return ["unbounded variable-length path"]
    upper_bound = bounds.split("..")[-1].strip()
    if not upper_bound.isdigit():
        return ["variable-length bound must be an integer literal"]
    if int(upper_bound) > MAX_HOPS:
        return [f"variable-length path longer than {MAX_HOPS} hops"]
    return []
