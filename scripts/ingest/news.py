"""Step 4 (role A, A3): news hypotheses, truth level `hypothesis`.

Source: GDELT DOC 2.0 API (JSON) for articles mentioning universe companies.
The small local model extracts `{subject, object, kind, sentence, confidence}`; names are resolved
through the full-text indexes; an unresolved name is counted, never invented.
Creates Article, MENTIONS and Claim {status: 'hypothesis'} with SUBJECT / OBJECT / EXTRACTED_FROM.
"""

from app.config import Settings
from app.database.neo4j import Neo4jClient


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Fetch articles, extract claims, write them; return counts."""
    raise NotImplementedError("A3: news.run")
