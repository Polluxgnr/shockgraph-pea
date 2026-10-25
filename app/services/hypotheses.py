"""LLM-extracted hypotheses (role A). Signatures are frozen: add methods, do not change these."""

from app.database.neo4j import Neo4jClient
from app.models.hypotheses import Hypothesis

# One row per claim. The CALL subquery picks the claim's most recent article without multiplying rows;
# the company filter uses the claim's own SUBJECT / OBJECT links (index seek on Company.isin first).
LIST_CLAIMS = """
MATCH (q:Claim)-[:SUBJECT]->(s:Company), (q)-[:OBJECT]->(o:Company)
WHERE ($status IS NULL OR q.status = $status)
  AND ($isin IS NULL OR s.isin = $isin OR o.isin = $isin)
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


class HypothesisService:
    """Reads Claim nodes with their provenance."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def list_claims(
        self, company_isin: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[Hypothesis]:
        """Claims (as subject or object of `company_isin` when given), most confident first."""
        rows = self.client.read(LIST_CLAIMS, {"isin": company_isin, "status": status, "limit": limit})
        return [Hypothesis(**row) for row in rows]
