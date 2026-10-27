"""Step 4 (role A, A3): supply-chain hypotheses from news headlines, truth level `hypothesis`.

Pipeline, for the largest PEA companies:
1. Headlines from Google News RSS (GDELT as an optional second source).
2. Two independent extractors produce (supplier, customer) candidates from each headline:
   - `rules`: a few precise patterns ("X wins Y contract", "Y awards X ..."), no model needed;
   - `llm`: the small local model with structured output, when Ollama answers.
3. Guards: both names must appear in the headline, resolve to two different companies of the graph
   (full-text index + name check). Anything else is dropped and counted, never invented.
4. One `Claim` per (supplier, customer, kind), linked to every `Article` that supports it. Confidence is the
   noisy-OR over distinct stories (normalised headlines, so syndicated copies count once), capped at 0.95; the
   claim is `confirmed` when two distinct stories from two publishers support it. A claim is never a fact.
Run alone with: python -m scripts.seed_data --only news
"""

import hashlib
import re
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.database.neo4j import Neo4jClient
from scripts.ingest.sources import news as news_sources

COMPANIES_TO_SCAN = 60
WINDOW_DAYS = 90
RULE_CONFIDENCE = 0.6
LLM_MAX_CONFIDENCE = 0.9
CONFIRMATION_SOURCES = 2
MAX_CLAIM_CONFIDENCE = 0.95  # a news claim is never certain, however many publishers repeat it

# Corporate-finance news is not a supply relation (JV partners, advisers on a stake sale, bidders).
NOT_SUPPLY = re.compile(
    r"\b(?:JV|joint venture|merger|acquisition|acquire[sd]?|takeover|stake|bid|IPO|buyback|spin-?off|"
    r"advis[eo]r?s?|lawsuit|sues)\b",
    re.I,
)

LEGAL_SUFFIXES = {
    "sa", "se", "ag", "nv", "plc", "inc", "corp", "corporation", "co", "ltd", "limited", "group", "holding",
    "holdings", "spa", "asa", "ab", "oyj", "the", "class", "a", "b", "company", "companies", "kgaa", "sca",
}  # fmt: skip

_NAME = r"([A-Z][\w&\-]*(?:\s+(?:[A-Z][\w&\-]*|&))*?)"  # lazy: a name followed by more pattern
_NAME_END = r"([A-Z][\w&\-]*(?:\s+(?:[A-Z][\w&\-]*|&))*)"  # greedy: a name that ends the pattern
_VERB_WIN = r"(?i:wins|win|awarded|secures|lands|bags|signs|receives)"
_VERB_AWARD = r"(?i:awards|gives|grants|picks|selects|chooses|taps|hires)"
RULES = [
    # "SLB wins 15-year TotalEnergies contract", "Vallourec awarded TotalEnergies deal"
    (re.compile(rf"\b{_NAME}\s+{_VERB_WIN}\b.*?\b{_NAME}(?:'s)?\s+(?i:contract|order|deal)"), "supplier_first"),
    # "SLB awarded digital drilling contract by TotalEnergies"
    (
        re.compile(rf"\b{_NAME}\s+{_VERB_WIN}\b.*?(?i:contract|order|deal)" rf"\s+(?i:by|from|with)\s+{_NAME_END}"),
        "supplier_first",
    ),
    # "TotalEnergies awards SLB ...", "Airbus picks Safran ..."
    (re.compile(rf"\b{_NAME}\s+{_VERB_AWARD}\s+{_NAME_END}"), "customer_first"),
    # "Petrolia to supply naphtha to Helios", "Petrolia will supply Helios"
    (
        re.compile(
            rf"\b{_NAME}\s+(?i:to supply|will supply|supplies|to deliver|will deliver)\b"
            rf"(?:.*?\b(?i:to|for)\s+)?\s*{_NAME_END}"
        ),
        "supplier_first",
    ),
]  # fmt: skip


@dataclass
class Candidate:
    """A (supplier, customer) pair read in one headline, before resolution."""

    supplier: str
    customer: str
    confidence: float
    extractor: str


class ExtractedRelation(BaseModel):
    """One supply relation stated in a headline (LLM structured output)."""

    supplier: str = Field(description="Company that sells or delivers, exactly as written in the headline")
    customer: str = Field(description="Company that buys or receives, exactly as written in the headline")
    confidence: float = Field(ge=0, le=1, description="How explicitly the headline states it")


class ExtractedRelations(BaseModel):
    """All supply relations stated in a headline (empty when there is none)."""

    relations: list[ExtractedRelation] = Field(default_factory=list)


PROMPT = (
    "Extract supply relations stated explicitly in this news headline. A supply relation means one company "
    "sells goods or services to another (contract, order, deal, delivery). Copy company names exactly as "
    "written. Do not guess: return an empty list when the headline does not state a supply relation.\n"
    "Headline: {headline}"
)


# --- extraction -----------------------------------------------------------------------------------


def extract_with_rules(headline: str) -> list[Candidate]:
    """Candidates found by the patterns; each pattern says which side is the supplier."""
    found = []
    for pattern, order in RULES:
        for match in pattern.finditer(headline):
            first, second = match.group(1).strip(), match.group(2).strip()
            supplier, customer = (first, second) if order == "supplier_first" else (second, first)
            found.append(Candidate(supplier, customer, RULE_CONFIDENCE, "rules"))
    return _dedupe(found)


def extract_with_llm(model, headline: str) -> list[Candidate]:
    """Candidates from the small model (structured output). Names not literally in the headline are dropped."""
    result = model.with_structured_output(ExtractedRelations).invoke(PROMPT.format(headline=headline))
    found = []
    for relation in result.relations:
        if not (_in_text(relation.supplier, headline) and _in_text(relation.customer, headline)):
            continue  # hallucination guard: the model may only copy names from the headline
        confidence = min(relation.confidence, LLM_MAX_CONFIDENCE)
        found.append(Candidate(relation.supplier.strip(), relation.customer.strip(), confidence, "llm"))
    return _dedupe(found)


def _in_text(name: str, text: str) -> bool:
    """Case-insensitive whole-word presence of a name in a text."""
    return bool(name.strip()) and re.search(rf"\b{re.escape(name.strip())}\b", text, re.I) is not None


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate per (supplier, customer), keeping the most confident."""
    best: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = (normalise(candidate.supplier), normalise(candidate.customer))
        if key[0] != key[1] and (key not in best or candidate.confidence > best[key].confidence):
            best[key] = candidate
    return list(best.values())


# --- resolution -----------------------------------------------------------------------------------


def normalise(name: str) -> str:
    """'TotalEnergies SE' -> 'totalenergies'; 'Schneider Electric S.E.' -> 'schneider electric'."""
    words = re.sub(r"[^\w\s&]", " ", name.lower()).split()
    return " ".join(word for word in words if word not in LEGAL_SUFFIXES)


# Company names that are also common words in Title Case headlines ("155mm Shell Order", "New Order").
# Refusing them loses a few true relations (a real Shell contract) but never invents one: precision first.
AMBIGUOUS_NAMES = {
    "shell", "orange", "target", "visa", "next", "block", "ball", "total", "sky", "snap", "gap", "unity",
    "new", "first", "general", "united", "national", "global", "international", "american", "royal", "major",
}  # fmt: skip


def names_match(mention: str, company_name: str) -> bool:
    """True when the mention and the company name agree once legal suffixes are removed.

    Either they are equal, or one starts with the other at a word boundary ('SLB' vs 'SLB Limited',
    'Schneider' vs 'Schneider Electric'). Never for mentions that are common words (AMBIGUOUS_NAMES),
    names under 3 letters, or one-word prefixes under 4 letters.
    """
    left, right = normalise(mention), normalise(company_name)
    if not left or not right or min(len(left), len(right)) < 3 or left in AMBIGUOUS_NAMES:
        return False
    if left == right:
        return True
    shorter = left if len(left) < len(right) else right
    if " " not in shorter and len(shorter) < 4:
        return False
    return right.startswith(left + " ") or left.startswith(right + " ")


def lucene_escape(text: str) -> str:
    """Escape Lucene special characters so a company name is searched literally."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


RESOLVE = """
CALL db.index.fulltext.queryNodes('company_name_ft', $query) YIELD node, score
RETURN node.isin AS isin, node.name AS name, score
ORDER BY score DESC
LIMIT 5
"""


class Resolver:
    """Mention -> company ISIN through the full-text index, with a name check and a cache."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client
        self.cache: dict[str, str | None] = {}

    def resolve(self, mention: str) -> str | None:
        """ISIN of the company named by the mention, or None when no candidate passes the name check."""
        key = normalise(mention)
        if key not in self.cache:
            rows = self.client.read(RESOLVE, {"query": lucene_escape(key)}) if key else []
            self.cache[key] = next((row["isin"] for row in rows if names_match(mention, row["name"])), None)
        return self.cache[key]


# --- claims ---------------------------------------------------------------------------------------


def claim_id(supplier_isin: str, customer_isin: str, kind: str = "SUPPLIES") -> str:
    """Stable id of a claim: the same relation found in several articles is one claim."""
    return "n-" + hashlib.sha1(f"{kind}|{supplier_isin}|{customer_isin}".encode()).hexdigest()[:12]


def noisy_or(confidences: list[float]) -> float:
    """Probability that at least one independent source is right: 1 - prod(1 - c)."""
    remaining = 1.0
    for confidence in confidences:
        remaining *= 1 - confidence
    return round(1 - remaining, 4)


def story_key(title: str) -> str:
    """Headline reduced to lowercase words: syndicated copies of one wire story get the same key."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def claim_confidence(evidence: list[tuple[str, float]]) -> float:
    """Same rule as the SCORE query: best confidence per distinct story (headline), noisy-OR, capped."""
    best: dict[str, float] = {}
    for title, confidence in evidence:
        key = story_key(title)
        best[key] = max(confidence, best.get(key, 0.0))
    return min(MAX_CLAIM_CONFIDENCE, noisy_or(list(best.values())))


def build_rows(articles: list[dict], extract, resolver: Resolver) -> tuple[list[dict], list[dict], dict]:
    """Turn headlines into article rows and claim-evidence rows; return them with drop counters."""
    article_rows, evidence_rows = [], []
    counters = {"not_supply": 0, "candidates": 0, "unresolved": 0, "same_company": 0}
    for article in articles:
        mentions: set[str] = set()
        if NOT_SUPPLY.search(article["title"]):
            counters["not_supply"] += 1
            article_rows.append({**article, "title_key": story_key(article["title"]), "mentions": []})
            continue
        for candidate in extract(article["title"]):
            counters["candidates"] += 1
            supplier, customer = resolver.resolve(candidate.supplier), resolver.resolve(candidate.customer)
            if not supplier or not customer:
                counters["unresolved"] += 1
                continue
            if supplier == customer:
                counters["same_company"] += 1
                continue
            mentions |= {supplier, customer}
            evidence_rows.append(
                {
                    "claim_id": claim_id(supplier, customer),
                    "supplier": supplier,
                    "customer": customer,
                    "url": article["url"],
                    "sentence": article["title"],
                    "confidence": candidate.confidence,
                    "extractor": candidate.extractor,
                }
            )
        article_rows.append({**article, "title_key": story_key(article["title"]), "mentions": sorted(mentions)})
    return article_rows, evidence_rows, counters


ARTICLES = """
UNWIND $rows AS row
MERGE (a:Article {url: row.url})
SET a.title = row.title, a.title_key = row.title_key, a.source = row.source,
    a.published_at = CASE WHEN row.published_at IS NULL THEN null ELSE date(row.published_at) END
WITH a, row
UNWIND row.mentions AS isin
MATCH (c:Company {isin: isin})
MERGE (a)-[:MENTIONS]->(c)
"""

EVIDENCE = """
UNWIND $rows AS row
MATCH (s:Company {isin: row.supplier}), (o:Company {isin: row.customer}), (a:Article {url: row.url})
MERGE (q:Claim {claim_id: row.claim_id})
  ON CREATE SET q.kind = 'SUPPLIES', q.status = 'hypothesis', q.sentence = row.sentence
MERGE (q)-[:SUBJECT]->(s)
MERGE (q)-[:OBJECT]->(o)
MERGE (q)-[e:EXTRACTED_FROM]->(a)
SET e.confidence = row.confidence, e.extractor = row.extractor
"""

# Recompute every news claim from all its evidence. One vote per distinct story (normalised headline, its best
# confidence): Google News lists one wire story under several publishers, and those copies are not independent.
# Confirmed = at least two distinct stories from at least two publishers. 'rejected' is a human decision, kept.
SCORE = """
MATCH (q:Claim)-[e:EXTRACTED_FROM]->(a:Article)
WHERE q.claim_id STARTS WITH 'n-'
WITH q, coalesce(a.title_key, toLower(a.title)) AS story, max(e.confidence) AS best,
     collect(DISTINCT a.source) AS publishers, collect(DISTINCT e.extractor) AS used
WITH q, collect(best) AS per_story, count(story) AS stories,
     reduce(all = [], names IN collect(publishers) | all + [name IN names WHERE NOT name IN all]) AS publishers,
     reduce(all = [], names IN collect(used) | all + [name IN names WHERE NOT name IN all]) AS extractors
WITH q, stories, size(publishers) AS sources, extractors,
     1 - reduce(remaining = 1.0, c IN per_story | remaining * (1 - c)) AS combined
SET q.confidence = CASE WHEN combined > $cap THEN $cap ELSE round(combined, 4) END,
    q.stories = stories, q.sources = sources, q.extractors = extractors, q.model = extractors[0],
    q.status = CASE WHEN q.status = 'rejected' THEN 'rejected'
                    WHEN stories >= $needed AND sources >= $needed THEN 'confirmed' ELSE 'hypothesis' END
RETURN count(q) AS claims, sum(CASE WHEN q.status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed
"""

KNOWN_URLS = "UNWIND $urls AS url MATCH (a:Article {url: url}) RETURN a.url AS url"

TOP_PEA = """
MATCH (c:Company {pea_eligible: true})
WHERE c.market_cap_eur IS NOT NULL
RETURN c.isin AS isin, c.name AS name
ORDER BY c.market_cap_eur DESC
LIMIT $limit
"""


def load(client: Neo4jClient, article_rows: list[dict], evidence_rows: list[dict]) -> dict:
    """Write articles, mentions and claims, then rescore every news claim."""
    client.write(ARTICLES, {"rows": article_rows})
    client.write(EVIDENCE, {"rows": evidence_rows})
    scored = client.write(SCORE, {"needed": CONFIRMATION_SOURCES, "cap": MAX_CLAIM_CONFIDENCE})
    return scored[0] if scored else {"claims": 0, "confirmed": 0}


def make_extractor(settings: Settings):
    """Rules always; plus the small local model when it is available. Returns (extract function, names)."""
    model = None
    try:
        from app.llm.client import get_chat_model  # pylint: disable=import-outside-toplevel

        if settings.llm_provider != "fake":
            model = get_chat_model(settings, "small")
            httpx.get(settings.ollama_base_url, timeout=3).raise_for_status()
    except (NotImplementedError, httpx.HTTPError, ImportError):
        model = None

    def extract(headline: str) -> list[Candidate]:
        found = extract_with_rules(headline)
        if model is not None:
            found += extract_with_llm(model, headline)
        return _dedupe(found)

    return extract, ["rules"] + (["llm"] if model is not None else [])


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Scan the news of the largest PEA companies and store supply hypotheses."""
    extract, extractors = make_extractor(settings)
    companies = client.read(TOP_PEA, {"limit": COMPANIES_TO_SCAN})
    articles: dict[str, dict] = {}
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as http:
        for company in companies:
            search_name = normalise(company["name"]).title() or company["name"]
            for article in news_sources.google_news(search_name, WINDOW_DAYS, client=http):
                if news_sources.is_recent(article["published_at"], WINDOW_DAYS):
                    articles.setdefault(article["url"], article)
            time.sleep(1)
    known = {row["url"] for row in client.read(KNOWN_URLS, {"urls": list(articles)})}
    new_articles = [article for url, article in articles.items() if url not in known]
    article_rows, evidence_rows, counters = build_rows(new_articles, extract, Resolver(client))
    scored = load(client, article_rows, evidence_rows)
    return {
        "extractors": extractors,
        "companies_scanned": len(companies),
        "articles_seen": len(articles),
        "articles_new": len(new_articles),
        **counters,
        "evidence": len(evidence_rows),
        **scored,
    }
