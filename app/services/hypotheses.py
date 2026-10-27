"""LLM-extracted hypotheses (role A). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.hypotheses import Hypothesis

# The start of the query depends on the filters, so that the planner can use an index:
# - a company: unique-index seek on Company.isin, then its claims (no scan of every claim);
# - a status only: index seek on Claim.status (`claim_status`);
# - nothing: all claims (the endpoint always has a LIMIT).
# A single query with `$isin IS NULL OR ...` cannot use an index: the planner must plan for both cases and scans.
# The fragments are constants chosen in Python, never built from user input; values stay parameters.
START_BY_COMPANY = """
MATCH (:Company {isin: $isin})<-[:SUBJECT|OBJECT]-(q:Claim)
WITH DISTINCT q
WHERE $status IS NULL OR q.status = $status
"""
START_BY_STATUS = "MATCH (q:Claim {status: $status})\n"
START_ALL = "MATCH (q:Claim)\n"

# One row per claim; the CALL subquery picks the most recent article without multiplying rows.
TAIL = """
MATCH (q)-[:SUBJECT]->(s:Company), (q)-[:OBJECT]->(o:Company)
CALL (q) {
  OPTIONAL MATCH (q)-[:EXTRACTED_FROM]->(a:Article)
  RETURN a ORDER BY a.published_at DESC LIMIT 1
}
RETURN q.claim_id AS claim_id, q.kind AS kind, s.isin AS subject_isin, s.name AS subject_name,
       o.isin AS object_isin, o.name AS object_name, q.confidence AS confidence, q.status AS status,
       q.sentence AS sentence, a.url AS source_url, a.title AS source_title,
       toString(a.published_at) AS published_at
ORDER BY confidence DESC, claim_id
LIMIT $limit
"""


def list_claims_query(company_isin: str | None, status: str | None) -> str:
    """The Cypher text for these filters (only constant fragments are combined)."""
    if company_isin:
        return START_BY_COMPANY + TAIL
    return (START_BY_STATUS if status else START_ALL) + TAIL


class HypothesisService:
    """Reads Claim nodes with their provenance."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def list_claims(
        self, company_isin: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[Hypothesis]:
        """Claims (as subject or object of `company_isin` when given), most confident first."""
        query = list_claims_query(company_isin, status)
        rows = self.client.read(query, {"isin": company_isin, "status": status, "limit": limit})
        return [Hypothesis(**row) for row in rows]
