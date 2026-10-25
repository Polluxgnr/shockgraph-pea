"""Tests of the news hypotheses (role A, A3). No network and no real model: feeds and LLM are faked."""

import pytest

from app.services.hypotheses import HypothesisService
from scripts.ingest import news
from scripts.ingest.sources.news import is_recent, parse_rss

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>SLB wins 15-year TotalEnergies contract - World Oil</title><link>https://example.org/a1</link>
<pubDate>Thu, 08 Oct 2026 07:00:00 GMT</pubDate><source url="https://worldoil.com">World Oil</source></item>
<item><title>No link here</title><pubDate>Thu, 08 Oct 2026 07:00:00 GMT</pubDate></item>
</channel></rss>"""


# --- sources --------------------------------------------------------------------------------------


def test_parse_rss_strips_publisher_and_skips_items_without_link():
    """The ' - Publisher' suffix of Google News titles is removed; the date becomes ISO."""
    assert parse_rss(RSS) == [
        {
            "url": "https://example.org/a1",
            "title": "SLB wins 15-year TotalEnergies contract",
            "source": "World Oil",
            "published_at": "2026-10-08",
        }
    ]


def test_is_recent():
    """Old articles are dropped; undated ones are kept."""
    from datetime import date  # pylint: disable=import-outside-toplevel

    assert is_recent("2026-10-01", 90, today=date(2026, 10, 25))
    assert not is_recent("2026-01-01", 90, today=date(2026, 10, 25))
    assert is_recent(None, 90)


# --- rule extraction (real headlines seen on 25 Oct 2026) -----------------------------------------


@pytest.mark.parametrize(
    ("headline", "pair"),
    [
        ("SLB wins 15-year TotalEnergies contract for digital well planning technology", ("SLB", "TotalEnergies")),
        ("Vallourec awarded TotalEnergies contract for Absheron gas field development", ("Vallourec", "TotalEnergies")),
        ("SLB Awarded Digital Drilling Contract by TotalEnergies", ("SLB", "TotalEnergies")),
        ("TotalEnergies awards SLB a drilling contract in Brazil", ("SLB", "TotalEnergies")),
        ("Petrolia to supply naphtha to Helios Chimie", ("Petrolia", "Helios Chimie")),
        ("Schneider Electric wins Airbus order for factory automation", ("Schneider Electric", "Airbus")),
    ],
)
def test_rules_find_the_supplier_and_the_customer(headline, pair):
    """The supplier/customer direction depends on the verb."""
    found = {(candidate.supplier, candidate.customer) for candidate in news.extract_with_rules(headline)}
    assert pair in found


@pytest.mark.parametrize(
    "headline",
    [
        "Azerbaijan: TotalEnergies announces Final Investment Decision for Absheron",
        "3 Energy Stocks to Buy and Hold for the Next Decade",
        "TotalEnergies shares fall as oil slides",
    ],
)
def test_rules_ignore_headlines_without_a_supply_relation(headline):
    """No verb of supply, no candidate."""
    assert not news.extract_with_rules(headline)


# --- LLM extraction with a fake model -------------------------------------------------------------


class FakeModel:
    """Mimics a LangChain chat model with structured output."""

    def __init__(self, relations: list[dict]) -> None:
        self.relations = relations
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        """Return self, remembering the schema."""
        self.schema = schema  # pylint: disable=attribute-defined-outside-init
        return self

    def invoke(self, prompt: str):
        """Return the scripted relations."""
        self.prompts.append(prompt)
        return self.schema(relations=self.relations)


def test_llm_names_not_in_the_headline_are_dropped():
    """The model may only copy names from the headline: an invented company is a hallucination."""
    model = FakeModel(
        [
            {"supplier": "SLB", "customer": "TotalEnergies", "confidence": 0.99},
            {"supplier": "Halliburton", "customer": "TotalEnergies", "confidence": 0.8},
        ]
    )
    found = news.extract_with_llm(model, "SLB wins 15-year TotalEnergies contract")
    assert [(item.supplier, item.customer, item.extractor) for item in found] == [("SLB", "TotalEnergies", "llm")]
    assert found[0].confidence == news.LLM_MAX_CONFIDENCE  # capped
    assert "SLB wins 15-year TotalEnergies contract" in model.prompts[0]


# --- resolution and scoring -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mention", "name", "expected"),
    [
        ("TotalEnergies", "TotalEnergies SE", True),
        ("SLB", "SLB Limited", True),
        ("Schneider", "Schneider Electric SE", True),
        ("Schneider Electric", "Schneider National Inc.", False),
        ("Total", "TotalEnergies SE", False),  # prefix inside a word does not count
        ("Shell", "Shell Plc", False),  # "155mm Shell Order": a common word, refused (real-run false positive)
        ("New", "New Oriental Education & Technology", False),  # "Receives New Order" (real-run false positive)
        ("RWE", "RWE AG", True),  # 3-letter names still match when they are the whole name
        ("BP", "BP PLC", False),  # under 3 letters: never matched automatically
    ],
)
def test_names_match(mention, name, expected):
    """Equality or prefix at a word boundary, after removing legal suffixes."""
    assert news.names_match(mention, name) is expected


def test_lucene_escape_and_claim_ids():
    """Names are searched literally; the same relation always gets the same id."""
    assert news.lucene_escape("AT&T (US)") == r"AT\&T \(US\)"
    assert news.claim_id("A", "B") == news.claim_id("A", "B") != news.claim_id("B", "A")
    assert news.claim_id("A", "B").startswith("n-")


def test_noisy_or():
    """Two independent 0.6 sources give 0.84."""
    assert news.noisy_or([0.6, 0.6]) == pytest.approx(0.84)
    assert news.noisy_or([]) == 0


def test_claim_confidence_counts_one_vote_per_publisher_and_never_reaches_one():
    """Ten syndicated copies of one story are one vote; many publishers are capped at 0.95."""
    assert news.claim_confidence([("Reuters", 0.6)] * 10) == pytest.approx(0.6)
    assert news.claim_confidence([("Reuters", 0.6), ("Reuters", 0.8), ("FT", 0.6)]) == pytest.approx(0.92)
    assert news.claim_confidence([(f"source{i}", 0.6) for i in range(10)]) == news.MAX_CLAIM_CONFIDENCE


@pytest.mark.parametrize(
    "headline",
    [
        "TotalEnergies Signs JV Deal with Petronas, Mitsui for Carbon Storage Areas in Malaysia",
        "Siemens Energy Taps Goldman Sachs for Steam Turbine Stake Sale",
        "Caixabank hires Morgan Stanley for potential Cetelem bid",
    ],
)
def test_corporate_finance_headlines_are_not_supply(headline):
    """Seen in the first real run: JVs and banking mandates are filtered before extraction."""
    resolver = FakeResolver({})
    _, evidence, counters = news.build_rows(
        [{"url": "u", "title": headline, "source": "s", "published_at": None}], news.extract_with_rules, resolver
    )
    assert not evidence and counters["not_supply"] == 1


class FakeResolver:
    """Resolves from a dictionary."""

    def __init__(self, known: dict[str, str]) -> None:
        self.known = known

    def resolve(self, mention: str):
        """Known mention -> ISIN."""
        return self.known.get(mention)


def test_build_rows_drops_unresolved_and_self_relations():
    """Only pairs of two different known companies become evidence."""
    articles = [
        {"url": "u1", "title": "SLB wins 15-year TotalEnergies contract", "source": "s", "published_at": None},
        {"url": "u2", "title": "Acme wins TotalEnergies contract", "source": "s", "published_at": None},
        {"url": "u3", "title": "TotalEnergies wins TotalEnergies contract", "source": "s", "published_at": None},
    ]
    resolver = FakeResolver({"SLB": "ZZSLB0000001", "TotalEnergies": "ZZTTE0000002"})
    article_rows, evidence, counters = news.build_rows(articles, news.extract_with_rules, resolver)
    assert len(article_rows) == 3 and article_rows[0]["mentions"] == ["ZZSLB0000001", "ZZTTE0000002"]
    assert [(row["supplier"], row["customer"]) for row in evidence] == [("ZZSLB0000001", "ZZTTE0000002")]
    assert counters["unresolved"] == 1


# --- service on the mini graph --------------------------------------------------------------------


@pytest.mark.neo4j
def test_list_claims_for_a_company(neo4j_client):
    """Autoroute: c1 (0.80) then c3 (0.30), as subject or object."""
    claims = HypothesisService(neo4j_client).list_claims(company_isin="DETEST000060")
    assert [(claim.claim_id, claim.confidence) for claim in claims] == [("c1", 0.80), ("c3", 0.30)]
    assert claims[0].source_url == "https://example.org/news/cuprum-autoroute-contract"
    assert claims[0].subject_name == "Cuprum Bergbau AG"


@pytest.mark.neo4j
def test_list_claims_by_status(neo4j_client):
    """Only c4 is rejected."""
    assert [claim.claim_id for claim in HypothesisService(neo4j_client).list_claims(status="rejected")] == ["c4"]


@pytest.mark.neo4j
def test_hypotheses_endpoint(api):
    """GET /api/hypotheses returns the claims, most confident first, limited."""
    response = api.get("/api/hypotheses", params={"limit": 2})
    assert response.status_code == 200
    assert [item["claim_id"] for item in response.json()] == ["c1", "c2"]


# --- loading news claims into the test database ---------------------------------------------------

CLEANUP = """
MATCH (n) WHERE n.isin IN ['ZZSLB0000001', 'ZZTTE0000002'] OR n.url STARTS WITH 'https://test-a.example/'
   OR n.claim_id = $claim
DETACH DELETE n
"""


@pytest.fixture
def two_companies(neo4j_client):
    """Two test companies, removed afterwards with everything the test created."""
    claim = news.claim_id("ZZSLB0000001", "ZZTTE0000002")
    neo4j_client.write(CLEANUP, {"claim": claim})
    neo4j_client.write(
        "CREATE (:Company {isin: 'ZZSLB0000001', name: 'Test SLB'}), (:Company {isin: 'ZZTTE0000002', name: 'Test TTE'})"
    )
    yield neo4j_client, claim
    neo4j_client.write(CLEANUP, {"claim": claim})


def evidence(url: str, confidence: float) -> dict:
    """One evidence row for the test relation."""
    return {
        "claim_id": news.claim_id("ZZSLB0000001", "ZZTTE0000002"),
        "supplier": "ZZSLB0000001",
        "customer": "ZZTTE0000002",
        "url": url,
        "sentence": "Test SLB wins Test TTE contract",
        "confidence": confidence,
        "extractor": "rules",
    }


def article(url: str, source: str) -> dict:
    """One article row mentioning both test companies."""
    return {"url": url, "title": "t", "source": source, "published_at": "2026-10-20",
            "mentions": ["ZZSLB0000001", "ZZTTE0000002"]}  # fmt: skip


@pytest.mark.neo4j
def test_claim_is_confirmed_by_two_publishers(two_companies):
    """One source: hypothesis. A second publisher: confirmed, confidence = noisy-OR. Fixture claims untouched."""
    client, claim = two_companies
    url_one, url_two = "https://test-a.example/1", "https://test-a.example/2"
    news.load(client, [article(url_one, "World Oil")], [evidence(url_one, 0.6)])
    first = client.read("MATCH (q:Claim {claim_id: $id}) RETURN q.status AS status, q.confidence AS c", {"id": claim})
    assert first == [{"status": "hypothesis", "c": pytest.approx(0.6)}]

    news.load(client, [article("https://test-a.example/1b", "World Oil")], [evidence("https://test-a.example/1b", 0.6)])
    same_source = client.read("MATCH (q:Claim {claim_id: $id}) RETURN q.status AS status", {"id": claim})
    assert same_source == [{"status": "hypothesis"}]  # a second article from the same publisher is not a confirmation

    news.load(client, [article(url_two, "Reuters")], [evidence(url_two, 0.6)])
    news.load(client, [article(url_two, "Reuters")], [evidence(url_two, 0.6)])  # idempotent
    second = client.read(
        "MATCH (q:Claim {claim_id: $id})-[e:EXTRACTED_FROM]->() RETURN q.status AS status, q.confidence AS c, count(e) AS n",
        {"id": claim},
    )
    assert second == [{"status": "confirmed", "c": pytest.approx(0.84), "n": 3}]
    fixture = client.read("MATCH (q:Claim {claim_id: 'c1'}) RETURN q.status AS status, q.confidence AS c")
    assert fixture == [{"status": "hypothesis", "c": 0.8}]
