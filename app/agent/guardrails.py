"""Cypher guardrails (role A): the last line of defence before LLM-generated Cypher reaches Neo4j."""

from dataclasses import dataclass, field

ALLOWED_LABELS = frozenset({"Company", "Fund", "Index", "Sector", "Industry", "Country", "Factor", "Article", "Claim"})
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


@dataclass
class ValidationResult:
    """Verdict of the validator. `reasons` is empty when the query is valid."""

    valid: bool
    reasons: list[str] = field(default_factory=list)


class CypherValidator:
    """Accepts only bounded, read-only queries over the known schema."""

    def validate(self, cypher: str) -> ValidationResult:
        """Check a query. See docs/roles/a.md (A4) for the full rule list."""
        raise NotImplementedError("A4: CypherValidator.validate")
