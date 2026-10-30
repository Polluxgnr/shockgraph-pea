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
    "TRANSACTIONS",  # IN TRANSACTIONS and IN CONCURRENT TRANSACTIONS
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

# Strings and comments in ONE left-to-right pass: whichever starts first wins, so the quote in `// it's` cannot
# open a fake string that would hide the code after the comment.
_STRING_OR_COMMENT = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|//[^\n]*|/\*.*?\*/", re.S)
_BACKTICK = re.compile(r"`([^`]*)`")
# The innermost bracket: right after `-` it is a relationship (`-[r:X*..3]->`), otherwise a list or a comprehension.
_INNER_BRACKET = re.compile(r"(-\s*)?\[([^\[\]]*)\]")
_LABEL_USE = re.compile(r"(?:\b[A-Za-z_]\w*|\()\s*:\s*(!?[A-Za-z_]\w*(?:\s*[:&|]\s*!?[A-Za-z_]\w*)*)")
# A quantifier after a node group `)` or, since Neo4j 5.9, right after a relationship (`]-`, `]->`, `-->`).
_QUANTIFIER = re.compile(r"(?:\)|-|>)\s*(\+|\*|\{\s*(\d*)\s*,\s*(\d*)\s*\}|\{\s*(\d+)\s*\})")
# `CALL db.labels()` and also `CALL db.labels` (arguments taken implicitly from the parameters); a subquery
# `CALL {` or `CALL (x) {` does not match.
_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)", re.I)
_UNION = re.compile(r"\bUNION(?:\s+ALL)?\b", re.I)
_RETURN = re.compile(r"\bRETURN\b", re.I)
# A label or type taken from an expression (`n:$($label)`, Neo4j 5.26): the allow-list could not check it.
_DYNAMIC_LABEL = re.compile(r":\s*\$")
_LIMIT = re.compile(r"\bLIMIT\b", re.I)
# An integer literal followed by the end, `}`, `;`, UNION or the next clause: `LIMIT 10 + 100000` is refused.
_STRICT_LIMIT = re.compile(
    r"\bLIMIT\s+(\d+)\s*(?=$|\}|;|\b(?:UNION|MATCH|OPTIONAL|WITH|UNWIND|CALL|RETURN|ORDER|SKIP)\b)", re.I
)
# A `{` that opens a map literal or projection (`{isin: $isin}`, `c {.name}`, `{}`), not a subquery or a quantifier.
_MAP_START = re.compile(r"\{\s*(?:\}|\.|[A-Za-z_]\w*\s*:(?!:))")
_MAP_KEY = re.compile(r"\s*(?:\.\*|\.[A-Za-z_]\w*|[A-Za-z_]\w*\s*:(?!:))")


@dataclass
class ValidationResult:
    """Verdict of the validator. `reasons` is empty when the query is valid."""

    valid: bool
    reasons: list[str] = field(default_factory=list)


def sanitize(cypher: str) -> str:
    """Blank string literals, remove comments, unquote backtick identifiers, collapse whitespace."""
    text = _STRING_OR_COMMENT.sub(lambda match: "''" if match.group(0)[0] in "'\"" else " ", cypher)
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

        def reduce(match: re.Match) -> str:
            if match.group(1) is not None:
                reasons.extend(_check_relationship(match.group(2)))
                return "-~"
            reasons.extend(_check_labels_and_quantifiers(match.group(2)))
            return "0"

        # Innermost brackets first, until none is left: a list inside a relationship (`-[:X* {w: [1]}]-`) is gone
        # before the relationship is checked, and every character is checked exactly once.
        remaining, previous = _strip_map_keys(text), None
        if _DYNAMIC_LABEL.search(remaining):
            reasons.append("dynamic labels and relationship types are not allowed")
        while remaining != previous:
            previous, remaining = remaining, _INNER_BRACKET.sub(reduce, remaining)
        reasons += _check_labels_and_quantifiers(remaining)
        return sorted(set(reasons), key=reasons.index)

    @staticmethod
    def _limit(upper: str) -> list[str]:
        """Every LIMIT is a single integer literal of at most MAX_LIMIT, and the final RETURN has one.

        "Final" is read at the top level only, and in every UNION branch: a LIMIT inside `COUNT { ... }` or in the
        last branch of a UNION does not bound the rows of the outer query.
        """
        reasons = []
        branches = _UNION.split(_top_level(upper))
        for branch in branches:
            returns = list(_RETURN.finditer(branch))
            if not returns or not _LIMIT.search(branch[returns[-1].start() :]):
                reasons.append(
                    f"each UNION branch needs a LIMIT after its RETURN (at most {MAX_LIMIT})"
                    if len(branches) > 1
                    else f"the final RETURN needs a LIMIT (at most {MAX_LIMIT})"
                )
        strict = {match.start(): int(match.group(1)) for match in _STRICT_LIMIT.finditer(upper)}
        for match in _LIMIT.finditer(upper):
            if match.start() not in strict:
                reasons.append("LIMIT must be a single integer literal")
            elif strict[match.start()] > MAX_LIMIT:
                reasons.append(f"LIMIT {strict[match.start()]} is above {MAX_LIMIT}")
        return sorted(set(reasons), key=reasons.index)


def _top_level(text: str) -> str:
    """The query with the inside of every `{...}` (subquery, map, quantifier) emptied, innermost first."""
    previous = None
    while text != previous:
        previous, text = text, re.sub(r"\{[^{}]*\}", "{}", text)
    return text


def _has_word(upper: str, phrase: str) -> bool:
    """True when the phrase appears as whole words (multi-word phrases allow any whitespace)."""
    pattern = r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b"
    return re.search(pattern, upper) is not None


def _strip_map_keys(text: str) -> str:
    """Remove the keys of map literals and projections, so a key is never read as a label (`beta: r`).

    Only inside a map: `RETURN c.name, c:User` keeps its label predicate. The values stay, so a pattern hidden in a
    value (`{p: [(a)-[*]-(b) | b]}`) is still checked.
    """
    pieces: list[str] = []
    in_map: list[bool] = []
    index = 0
    while index < len(text):
        char = text[index]
        pieces.append(char)
        index += 1
        if char in "([{":
            in_map.append(char == "{" and _MAP_START.match(text, index - 1) is not None)
        elif char in ")]}" and in_map:
            in_map.pop()
        if char in "{," and in_map and in_map[-1]:
            key = _MAP_KEY.match(text, index)
            if key:
                index = key.end()
    return "".join(pieces)


def _check_labels_and_quantifiers(text: str) -> list[str]:
    """Labels outside relationships must exist; quantified path patterns must be bounded."""
    reasons = []
    for expression in _LABEL_USE.findall(text):
        for label in re.split(r"[:&|!\s]+", expression):
            if label and label not in ALLOWED_LABELS | SUB_LABELS:
                reasons.append(f"unknown label {label}")
    for plus_or_star, _, high, exact in _QUANTIFIER.findall(text):
        if plus_or_star in ("+", "*") or (plus_or_star.startswith("{") and "," in plus_or_star and not high):
            reasons.append("unbounded quantified path")
        elif int(high or exact or 0) > MAX_HOPS:
            reasons.append(f"quantified path longer than {MAX_HOPS} hops")
    return reasons


def _check_relationship(body: str) -> list[str]:
    """Check one `[...]` pattern such as `r:SENSITIVE_TO|CONSUMES*1..3 {w: 0}`: types and length bounds."""
    body = re.split(r"\bWHERE\b", body, maxsplit=1, flags=re.I)[0]
    previous = None
    while body != previous:
        previous, body = body, re.sub(r"\{[^{}]*\}", " ", body)
    body = body.strip()
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
