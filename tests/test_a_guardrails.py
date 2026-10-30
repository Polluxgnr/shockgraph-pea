"""Tests of the Cypher guardrail (role A, A4): what the LLM may and may not send to Neo4j."""

import pytest

from app.agent.guardrails import CypherValidator, sanitize

ACCEPTED = [
    "MATCH (c:Company)-[r:SENSITIVE_TO]->(f:Factor {code: $code}) WHERE r.r2 >= 0.1 "
    "RETURN c.name AS name, r.beta AS beta ORDER BY abs(r.beta) DESC LIMIT 10",
    "MATCH p = shortestPath((a:Company {isin: $a})-[*..4]-(b:Company {isin: $b})) RETURN p LIMIT 1",
    "MATCH (f:Fund)-[:TRACKS]->(:Index)-[w:CONSTITUENT]->(c:Company) WHERE f.pea_eligible "
    "RETURN f.name, sum(w.weight) AS total LIMIT 20",
    "CALL db.index.fulltext.queryNodes('company_name_ft', 'petrol*') YIELD node, score "
    "RETURN node.name, score LIMIT 5",
    "MATCH (c:Company)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(s:Sector) "
    "WITH s, count(c) AS companies RETURN s.name, companies ORDER BY companies DESC LIMIT 11",
    "UNWIND $isins AS isin MATCH (c:Company {isin: isin}) RETURN c.name LIMIT 50",
    "MATCH (q:Claim {status: 'hypothesis'})-[:SUBJECT]->(s:Company), (q)-[:OBJECT]->(o:Company) "
    "RETURN s.name, o.name, q.confidence LIMIT 10",
    "MATCH (c:Company) WHERE c.name CONTAINS 'CREATE' OR c.name = \"DELETE me\" RETURN c LIMIT 3",
    "MATCH (x:Factor:Commodity) OPTIONAL MATCH (c:Company)-[:PRODUCES|CONSUMES]->(x) "
    "RETURN x.code, count(c) LIMIT 10;",
    "MATCH (c:Company {isin: $isin}) CALL { WITH c MATCH (c)-[:CORRELATED_WITH]-(o:Company) "
    "RETURN o ORDER BY o.name LIMIT 5 } RETURN c.name, collect(o.name) AS peers LIMIT 1",
    # map projection (review of #23): `beta: r` is not a label
    "MATCH (c:Company)-[r:SENSITIVE_TO]->(x:Factor) RETURN c {.name, beta: r.beta} AS row LIMIT 5",
    # LIMIT followed by another clause is still a plain integer
    "MATCH (c:Company) WITH c ORDER BY c.market_cap_eur DESC LIMIT 10 MATCH (c)-[:IN_INDUSTRY]->(n:Industry) "
    "RETURN c.name, n.name LIMIT 10",
    # a bounded relationship quantifier is fine
    "MATCH (a:Company {isin: $isin})-[:CORRELATED_WITH]-{1,3}(b:Company) RETURN b.name LIMIT 5",
    # a predicate inside the relationship, and a list in a map projection (third review of #23)
    "MATCH (a:Company)-[r:SENSITIVE_TO WHERE r.r2 > 0.1]->(f:Factor) RETURN f.code LIMIT 5",
    "MATCH (c:Company) RETURN c {.name, sectors: [(c)-[:IN_INDUSTRY]->(i:Industry) | i.name]} AS row LIMIT 5",
    # UNION with a LIMIT in every branch, a scoped CALL subquery, parameters in maps (fourth review of #23)
    "MATCH (c:Company) RETURN c.name AS name LIMIT 5 UNION MATCH (f:Fund) RETURN f.name AS name LIMIT 5",
    "MATCH (c:Company) CALL (c) { MATCH (c)-[:IN_INDUSTRY]->(i:Industry) RETURN i LIMIT 1 } "
    "RETURN c.name, i.name LIMIT 5",
    "MATCH (c:Company {isin: $isin}) RETURN c {.name, cap: $cap} AS row LIMIT 5",
]

REFUSED = [
    ("MATCH (c:Company) DETACH DELETE c", "write clause DELETE"),
    ("CREATE (:Company {isin: 'X'})", "write clause CREATE"),
    ("MERGE (c:Company {isin: 'X'}) RETURN c LIMIT 1", "write clause MERGE"),
    ("MATCH (c:Company) SET c.pea_eligible = true RETURN c LIMIT 1", "write clause SET"),
    ("MATCH (c:Company) REMOVE c.ticker RETURN c LIMIT 1", "write clause REMOVE"),
    ("DROP INDEX company_name_ft", "write clause DROP"),
    ("LOAD CSV FROM 'file:///x.csv' AS row RETURN row LIMIT 1", "write clause LOAD CSV"),
    ("MATCH (c:Company) FOREACH (x IN [1] | SET c.x = 1) RETURN c LIMIT 1", "write clause FOREACH"),
    ("MATCH (c:Company) CALL { WITH c SET c.x = 1 } IN TRANSACTIONS RETURN c LIMIT 1", "TRANSACTIONS"),
    ("MATCH (c:Company) RETURN c LIMIT 5; MATCH (n) DETACH DELETE n", "several statements"),
    ("CALL dbms.listConfig() YIELD name RETURN name LIMIT 5", "procedure dbms.listConfig"),
    ("CALL apoc.cypher.run('MATCH (n) DETACH DELETE n', {}) YIELD value RETURN value LIMIT 1", "apoc"),
    ("RETURN apoc.text.join(['a'], ',') AS x LIMIT 1", "apoc"),
    ("CALL gds.pageRank.write('market', {writeProperty: 'pr'}) YIELD nodePropertiesWritten RETURN 1 LIMIT 1", "gds"),
    ("CALL gds.graph.drop('market') YIELD graphName RETURN graphName LIMIT 1", "gds procedures may only stream"),
    ("MATCH (u:User) RETURN u LIMIT 5", "unknown label User"),
    ("MATCH (c:Company)-[:OWNS]->(x) RETURN x LIMIT 5", "unknown relationship type OWNS"),
    ("MATCH (a:Company)-[*]-(b:Company) RETURN b LIMIT 5", "unbounded variable-length path"),
    ("MATCH (a:Company)-[:CORRELATED_WITH*2..]-(b) RETURN b LIMIT 5", "unbounded variable-length path"),
    ("MATCH (a:Company)-[:CORRELATED_WITH*..12]-(b) RETURN b LIMIT 5", "longer than 6 hops"),
    ("MATCH (a:Company)((:Company)-[:CORRELATED_WITH]-(:Company))+(b) RETURN b LIMIT 5", "unbounded quantified path"),
    ("MATCH (c:Company) RETURN c", "needs a LIMIT"),
    ("MATCH (c:Company) RETURN c LIMIT 1000", "LIMIT 1000 is above 100"),
    ("MATCH (c:Company) RETURN c LIMIT $limit", "single integer literal"),
    ("MATCH (c:Company) /* hidden */ DETACH /* x */ DELETE c", "write clause DELETE"),
    ("SHOW DATABASES YIELD name RETURN name LIMIT 5", "a query must start with"),
    ("USE system MATCH (n) RETURN n LIMIT 1", "clause USE"),
    ("MATCH (c:Company) WITH c LIMIT 5", "must RETURN"),
    ("", "empty query"),
    # bypasses found in the review of #23 (they compile on Neo4j 5.26)
    ("MATCH (a:Company {isin: $isin})-[:CORRELATED_WITH]-{1,50}(b:Company) RETURN b.name LIMIT 5", "longer than 6"),
    ("MATCH (a:Company {isin: $isin})-[:CORRELATED_WITH]-+(b:Company) RETURN b.name LIMIT 5", "unbounded quantified"),
    ("MATCH (a:Company)-->*(b:Company) RETURN b LIMIT 5", "unbounded quantified"),
    ("MATCH (c:Company) RETURN c.name LIMIT 10 + 100000", "single integer literal"),
    # patterns hidden in map values (second review of #23)
    (
        "MATCH (c:Company) RETURN c {.name, peers: [(c)-[:CORRELATED_WITH*]-(o:Company) | o.name]} AS row LIMIT 5",
        "unbounded variable-length path",
    ),
    ("RETURN {peers: [(c:Company)-[:CORRELATED_WITH*]-(o:Company) | o.name]} AS row LIMIT 5", "unbounded"),
    ("RETURN {who: [(u:User) | u.name]} AS row LIMIT 5", "unknown label User"),
    # a list inside the relationship brackets (third review of #23): checked innermost first
    ("MATCH (a:Company)-[:CORRELATED_WITH* {rho: [0.5][0]}]-(b:Company) RETURN b.name LIMIT 5", "unbounded"),
    ("MATCH (a:Company)<-[:CORRELATED_WITH* {w: [1]}]-(b:Company) RETURN b.name LIMIT 5", "unbounded"),
    (
        "MATCH (a:Company) WHERE EXISTS { (a)-[:CORRELATED_WITH* {w: [1]}]-(:Company) } RETURN a.name LIMIT 5",
        "unbounded variable-length path",
    ),
    ("MATCH (a:Company)-[r:CORRELATED_WITH* {w: [1]}]-(b:Company) RETURN b.name LIMIT 5", "unbounded"),
    # a label predicate after a comma is not a map key
    ("MATCH (c:Company) RETURN c.name, c:User AS flag LIMIT 5", "unknown label User"),
    ("MATCH (c:Company) RETURN [x IN [1, 2] WHERE x > 0 | c:User] AS flags LIMIT 5", "unknown label User"),
    ("MATCH (c:Company) RETURN [p = (c)-->{1,50}(b) | b] AS far LIMIT 5", "longer than 6"),
    # a LIMIT only in the last UNION branch bounds that branch only (fourth review of #23)
    (
        "MATCH (c:Company) RETURN c.name AS name UNION MATCH (f:Fund) RETURN f.name AS name LIMIT 5",
        "each UNION branch needs a LIMIT",
    ),
    (
        "MATCH (c:Company) RETURN c.name AS name UNION ALL MATCH (f:Fund) RETURN f.name AS name LIMIT 1",
        "each UNION branch needs a LIMIT",
    ),
    # found while hunting for more: a LIMIT inside COUNT { } is not the outer LIMIT
    ("MATCH (c:Company) RETURN COUNT { MATCH (c)--(x) RETURN x LIMIT 1 } AS n", "the final RETURN needs a LIMIT"),
    # the quote of a comment must not open a string that hides the next line
    ("MATCH (c:Company) // it's fine\nMATCH (a)-[*]-(b) RETURN b LIMIT 1", "unbounded variable-length path"),
    ("CALL db.createLabel YIELD x RETURN x LIMIT 1", "procedure db.createLabel is not allowed"),
    ("MATCH (n:$($label)) RETURN n LIMIT 5", "dynamic labels"),
    (
        "MATCH (c:Company) CALL { WITH c RETURN c.name AS n } IN CONCURRENT TRANSACTIONS RETURN n LIMIT 5",
        "TRANSACTIONS",
    ),
]


@pytest.fixture(scope="module")
def validator() -> CypherValidator:
    """One validator for the module."""
    return CypherValidator()


@pytest.mark.parametrize("cypher", ACCEPTED)
def test_accepted(validator, cypher):
    """Bounded read-only queries over the schema pass."""
    result = validator.validate(cypher)
    assert result.valid, result.reasons


@pytest.mark.parametrize(("cypher", "reason"), REFUSED)
def test_refused_with_reason(validator, cypher, reason):
    """Each dangerous or unbounded query is refused, and the expected reason is among the reasons."""
    result = validator.validate(cypher)
    assert not result.valid
    assert any(reason in item for item in result.reasons), result.reasons


def test_at_least_twenty_refusals_and_ten_acceptances():
    """The grid asks for a real evaluation of the guardrail."""
    assert len(REFUSED) >= 20 and len(ACCEPTED) >= 10


def test_sanitize_blanks_strings_and_comments():
    """Keywords inside strings disappear; comments are removed but what they separate stays visible."""
    text = sanitize("MATCH (c) // DELETE\nWHERE c.name = 'CREATE' /* SET */ RETURN `my label`")
    assert "CREATE" not in text and "SET" not in text and "DELETE" not in text
    assert text.endswith("RETURN my_label")


def test_all_reasons_are_reported(validator):
    """A query breaking several rules gets every reason, which the agent trace shows."""
    result = validator.validate("MATCH (u:User)-[*]-(x) DETACH DELETE u")
    assert len(result.reasons) >= 4


def test_validate_endpoint(api_fake):
    """POST /api/agent/validate-cypher returns the verdict without running the query."""
    response = api_fake.post("/api/agent/validate-cypher", json={"cypher": "MATCH (c:Company) DETACH DELETE c"})
    body = response.json()
    assert response.status_code == 200 and body["valid"] is False and body["reasons"]


@pytest.mark.neo4j
@pytest.mark.parametrize("cypher", ACCEPTED)
def test_accepted_queries_compile_on_neo4j(neo4j_client, cypher):
    """EXPLAIN plans the query without running it: accepted queries are real, valid Cypher."""
    params = {"code": "BRENT", "a": "FRTEST000011", "b": "DETEST000078", "isins": [], "isin": "FRTEST000011"}
    neo4j_client.read("EXPLAIN " + cypher.rstrip(";"), params)


def test_count_subquery_in_a_projection_gives_only_the_real_reason(validator):
    """Second review of #23: `reach: COUNT {...}` must not be read as the label COUNT."""
    result = validator.validate(
        "MATCH (c:Company) RETURN c {.name, reach: COUNT { (c)-[:CORRELATED_WITH*]-(:Company) }} AS row LIMIT 5"
    )
    assert not result.valid
    assert result.reasons == ["unbounded variable-length path"], result.reasons


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (a:Company)-[:CORRELATED_WITH* {rho: [0.5][0]}]-(b:Company) RETURN b.name LIMIT 5",
        "MATCH (a:Company)-[r:CORRELATED_WITH* {w: [1]}]-(b:Company) RETURN b.name LIMIT 5",
    ],
)
def test_list_in_relationship_gives_only_the_real_reason(validator, cypher):
    """The relationship is checked after its lists are gone: no `unknown label CORRELATED_WITH` in the trace."""
    assert validator.validate(cypher).reasons == ["unbounded variable-length path"]


# Neo4j 5.26 reads these as `-` or `<` in a pattern (fifth review of #23): each one must not hide a relationship.
UNICODE_DASHES = ["­", "‐", "‑", "‒", "–", "—", "―", "﹘", "﹣", "－"]
UNICODE_ARROW_HEADS = ["⟨", "〈", "﹤", "＜"]


@pytest.mark.parametrize("dash", UNICODE_DASHES)
def test_unicode_dash_cannot_hide_an_unbounded_path(validator, dash):
    """`(a)–[:X*]–(b)`: refused for the character and for what Neo4j would have run."""
    cypher = f"MATCH (a:Company){dash}[:CORRELATED_WITH*]{dash}(b:Company) RETURN b.name LIMIT 5"
    assert validator.validate(cypher).reasons == [
        "non-ASCII characters are only allowed inside strings",
        "unbounded variable-length path",
    ]


@pytest.mark.parametrize("head", UNICODE_ARROW_HEADS)
def test_unicode_arrow_head_is_refused(validator, head):
    """`<` look-alikes are folded, and the query is refused for its characters."""
    cypher = f"MATCH (a:Company){head}-[:CORRELATED_WITH*]-(b:Company) RETURN b.name LIMIT 5"
    reasons = validator.validate(cypher).reasons
    assert reasons[0] == "non-ASCII characters are only allowed inside strings"
    assert "unbounded variable-length path" in reasons


def test_unicode_inside_strings_is_fine(validator):
    """A company name with an en dash or an accent stays a valid string."""
    assert validator.validate("MATCH (c:Company) WHERE c.name = 'Saint–Gobain Société' RETURN c LIMIT 5").valid


def test_unicode_in_quantifier_and_subquery(validator):
    """The dash family also reaches quantifiers and EXISTS subqueries."""
    for cypher in [
        "MATCH (a:Company)-[:CORRELATED_WITH]–{1,}(b:Company) RETURN b LIMIT 5",
        "MATCH (a:Company) WHERE EXISTS { (a)–[:CORRELATED_WITH*]–(:Company) } RETURN a LIMIT 5",
    ]:
        reasons = validator.validate(cypher).reasons
        assert reasons[0] == "non-ASCII characters are only allowed inside strings" and len(reasons) == 2
