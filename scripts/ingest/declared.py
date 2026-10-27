"""Step 2 (role A, A2): declared exposures, truth level `fact`.

- `Factor` nodes from `data/seed/factors.csv`, each with one sub-label (`:Commodity`, `:Currency`, `:Rate`).
- Industry level: `data/seed/industry_factors.csv`, a hand-written map with one justification per line
  (airlines CONSUME BRENT, miners PRODUCE COPPER...). Currencies and rates are never declared: a company's
  exposure to them is measured from prices (role C), not asserted.
- Company level, curated: `data/seed/company_factors.csv`, hand-checked links where an industry line would be
  false for many members (not every Metals & Mining company produces gold; not every luxury house buys it).
- Company level: Wikidata products (P1056) looked up by ISIN, matched to factors through
  `data/seed/product_factors.csv` (verified Q-ids first, then whole-word keywords). Unmatched products are
  counted, never guessed.
Run alone with: python -m scripts.seed_data --only declared
"""

import re
from collections import Counter
from datetime import datetime, timezone

from app.config import Settings
from app.database.neo4j import Neo4jClient
from scripts.ingest.sources import wikidata
from scripts.ingest.universe import read_seed

SUB_LABELS = {"commodity": "Commodity", "currency": "Currency", "rate": "Rate"}
RELATIONS = ("PRODUCES", "CONSUMES")


def factor_for_product(product_id: str | None, label: str | None, product_map: list[dict]) -> str | None:
    """Factor code for a Wikidata product: exclusions first, then exact Q-id, then a whole-word keyword."""
    text = (label or "").lower()
    if any(row["match"] == f"exclude:{text}" for row in product_map):
        return None
    for row in product_map:
        if product_id and row["match"] == product_id:
            return row["factor"]
    for row in product_map:
        if row["match"].startswith("keyword:"):
            keyword = row["match"].split(":", 1)[1]
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return row["factor"]
    return None


def company_links(rows: list[dict], product_map: list[dict]) -> tuple[list[dict], list[dict], Counter]:
    """Split Wikidata rows into (entity ids, PRODUCES links, unmatched product labels)."""
    entities = {row["isin"]: row["company"] for row in rows if row["company"]}
    links: dict[tuple[str, str], dict] = {}
    unmatched: Counter = Counter()
    for row in rows:
        if not row["product"]:
            continue
        factor = factor_for_product(row["product"], row["label"], product_map)
        if factor:
            links.setdefault(
                (row["isin"], factor),
                {"isin": row["isin"], "factor": factor, "product": row["label"], "product_id": row["product"]},
            )
        else:
            unmatched[row["label"] or row["product"]] += 1
    entity_rows = [{"isin": isin, "wikidata_id": qid} for isin, qid in entities.items()]
    return entity_rows, list(links.values()), unmatched


FACTORS = """
UNWIND $rows AS row
MERGE (x:Factor {code: row.code})
SET x.name = row.name, x.kind = row.kind, x.unit = row.unit, x.source = row.source, x.source_id = row.source_id
WITH x, row
FOREACH (_ IN CASE WHEN row.kind = 'commodity' THEN [1] ELSE [] END | SET x:Commodity)
FOREACH (_ IN CASE WHEN row.kind = 'currency' THEN [1] ELSE [] END | SET x:Currency)
FOREACH (_ IN CASE WHEN row.kind = 'rate' THEN [1] ELSE [] END | SET x:Rate)
"""

# Relationship types cannot be parameters: one fixed query per type, chosen from RELATIONS only.
INDUSTRY_LINKS = {relation: f"""
UNWIND $rows AS row
MATCH (n:Industry {{code: row.industry_code}})
MATCH (x:Factor {{code: row.factor}})
MERGE (n)-[r:{relation}]->(x)
SET r.source = 'sector_map', r.rationale = row.rationale, r.run = $run
RETURN row.industry_code AS key
""" for relation in RELATIONS}

# Hand-checked company-level links (jewellers buy gold, named miners produce copper...): where an industry line
# would be false for many of its members.
CURATED_LINKS = {relation: f"""
UNWIND $rows AS row
MATCH (c:Company {{isin: row.isin}})
MATCH (x:Factor {{code: row.factor}})
MERGE (c)-[r:{relation}]->(x)
SET r.source = 'curated', r.rationale = row.rationale, r.run = $run
RETURN row.isin AS key
""" for relation in RELATIONS}

ENTITIES = """
UNWIND $rows AS row
MATCH (c:Company {isin: row.isin})
SET c.wikidata_id = row.wikidata_id
"""

COMPANY_PRODUCES = """
UNWIND $rows AS row
MATCH (c:Company {isin: row.isin})
MATCH (x:Factor {code: row.factor})
MERGE (c)-[r:PRODUCES]->(x)
// A hand-checked link written in this run keeps its source; Wikidata only adds the product it names.
SET r.source = CASE WHEN r.source = 'curated' AND r.run = $run THEN 'curated' ELSE 'wikidata' END,
    r.product = row.product, r.wikidata_product = row.product_id, r.run = $run
"""

STALE = """
MATCH ()-[r:PRODUCES|CONSUMES]->(:Factor)
WHERE r.run IS NOT NULL AND r.run <> $run AND r.source IN ['sector_map', 'curated', 'wikidata']
DELETE r
"""


def write_by_relation(client: Neo4jClient, queries: dict, rows: list[dict], key: str, run_id: str) -> tuple[int, list]:
    """Run the per-relation queries; return the number of links written and the keys that wrote none."""
    written = []
    for relation in RELATIONS:
        subset = [row for row in rows if row["relation"] == relation]
        written += [record["key"] for record in client.write(queries[relation], {"rows": subset, "run": run_id})]
    missing = sorted({row[key] for row in rows} - set(written))
    return len(written), missing


def load(
    client: Neo4jClient,
    factors: list[dict],
    industry_rows: list[dict],
    wikidata_rows: list[dict],
    curated_rows: list[dict] | None = None,
) -> dict:
    """Write factors, industry, curated and Wikidata links; remove links from older runs; report what was written."""
    run_id = datetime.now(timezone.utc).isoformat(timespec="seconds")
    client.write(FACTORS, {"rows": factors})
    industry_written, industry_missing = write_by_relation(
        client, INDUSTRY_LINKS, industry_rows, "industry_code", run_id
    )
    curated_written, curated_missing = write_by_relation(client, CURATED_LINKS, curated_rows or [], "isin", run_id)
    entities, links, unmatched = company_links(wikidata_rows, read_seed("product_factors.csv"))
    client.write(ENTITIES, {"rows": entities})
    client.write(COMPANY_PRODUCES, {"rows": links, "run": run_id})
    client.write(STALE, {"run": run_id})
    return {
        "factors": len(factors),
        "industry_links_written": industry_written,
        "industry_codes_without_link": industry_missing,
        "curated_links_written": curated_written,
        "curated_isins_not_in_graph": curated_missing,
        "companies_on_wikidata": len(entities),
        "company_produces": len(links),
        "unmatched_products": sum(unmatched.values()),
        "top_unmatched": [label for label, _ in unmatched.most_common(5)],
    }


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Load factors, the industry map and the Wikidata products of every company in the graph."""
    del settings
    isins = [row["isin"] for row in client.read("MATCH (c:Company) RETURN c.isin AS isin ORDER BY isin")]
    wikidata_rows = wikidata.products_by_isin(isins)
    return load(
        client,
        read_seed("factors.csv"),
        read_seed("industry_factors.csv"),
        wikidata_rows,
        read_seed("company_factors.csv"),
    )
