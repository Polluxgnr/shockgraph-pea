"""Read-only company and factor queries owned by role B."""

import base64
import json
import re
from typing import Any

from app.database.neo4j import Neo4jClient
from app.models.common import Page
from app.models.companies import CompanyDetail, CompanySummary, Exposure, FactorSummary

_LUCENE_SPECIAL = re.compile(r"([+\-!(){}\[\]^\"~*?:\\/]|&&|\|\|)")


def _fulltext_term(value: str) -> str:
    """Escape Lucene operators and request prefix matching."""
    escaped = _LUCENE_SPECIAL.sub(r"\\\1", value.strip())
    return f"{escaped}*"


def _encode_cursor(market_cap: float, isin: str) -> str:
    """Encode the stable company sort key as an opaque cursor."""
    payload = json.dumps({"cap": market_cap, "isin": isin}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    """Decode a cursor, raising ValueError for malformed input."""
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return float(payload["cap"]), str(payload["isin"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid company pagination cursor") from exc


def _company_summary(row: dict[str, Any]) -> CompanySummary:
    """Convert a Neo4j row to the public summary model."""
    return CompanySummary(
        isin=row["isin"],
        name=row["name"],
        ticker=row.get("ticker"),
        sector=row.get("sector"),
        country=row.get("country"),
        pea_eligible=bool(row.get("pea_eligible")),
    )


class CompanyService:
    """Read-only queries about companies."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def search(
        self,
        query: str | None = None,
        sector: str | None = None,
        pea_only: bool = False,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[CompanySummary]:
        """Search companies with filters and stable cursor pagination."""
        cursor_cap, cursor_isin = _decode_cursor(cursor)
        prefix = (
            "CALL db.index.fulltext.queryNodes('company_name_ft', $query) " "YIELD node AS c, score "
            if query
            else "MATCH (c:Company) "
        )
        cypher = prefix + """
        OPTIONAL MATCH (c)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(s:Sector)
        OPTIONAL MATCH (c)-[:HEADQUARTERED_IN]->(country:Country)
        WITH DISTINCT c, s, country, coalesce(c.market_cap_eur, -1.0) AS cap
        WHERE ($sector IS NULL OR s.code = $sector OR toLower(s.name) = toLower($sector))
          AND (NOT $pea_only OR c.pea_eligible = true)
          AND ($cursor_cap IS NULL OR cap < $cursor_cap
               OR (cap = $cursor_cap AND c.isin > $cursor_isin))
        RETURN c.isin AS isin, c.name AS name, c.ticker AS ticker,
               s.name AS sector, country.code AS country,
               c.pea_eligible AS pea_eligible, cap
        ORDER BY cap DESC, isin
        LIMIT $fetch_limit
        """
        rows = self.client.read(
            cypher,
            {
                "query": _fulltext_term(query) if query else None,
                "sector": sector,
                "pea_only": pea_only,
                "cursor_cap": cursor_cap,
                "cursor_isin": cursor_isin,
                "fetch_limit": limit + 1,
            },
        )
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(float(last["cap"]), last["isin"])
        return Page(items=[_company_summary(row) for row in page_rows], next_cursor=next_cursor)

    def get(self, isin: str) -> CompanyDetail | None:
        """Return one company with its classification, country and indexes."""
        rows = self.client.read(
            """
            MATCH (c:Company {isin: $isin})
            OPTIONAL MATCH (c)-[:IN_INDUSTRY]->(industry:Industry)-[:PART_OF]->(sector:Sector)
            OPTIONAL MATCH (c)-[:HEADQUARTERED_IN]->(country:Country)
            OPTIONAL MATCH (index:Index)-[:CONSTITUENT]->(c)
            RETURN c.isin AS isin, c.name AS name, c.ticker AS ticker,
                   sector.name AS sector, industry.name AS industry, country.code AS country,
                   c.pea_eligible AS pea_eligible, c.lei AS lei,
                   c.market_cap_eur AS market_cap_eur,
                   [code IN collect(DISTINCT index.code) WHERE code IS NOT NULL] AS indexes
        """,
            {"isin": isin},
        )
        if not rows:
            return None
        rows[0]["indexes"] = sorted(rows[0]["indexes"])
        return CompanyDetail(**rows[0])

    def exposures(self, isin: str, min_r2: float = 0.0) -> list[Exposure]:
        """Return estimated and declared factor links with truth levels."""
        rows = self.client.read(
            """
            MATCH (c:Company {isin: $isin})-[rel:SENSITIVE_TO]->(factor:Factor)
            WHERE coalesce(rel.r2, 0.0) >= $min_r2
            RETURN c.isin AS company_isin, c.name AS company_name,
                   factor.code AS factor_code, factor.name AS factor_name,
                   'SENSITIVE_TO' AS relation, 'estimate' AS truth,
                   rel.beta AS beta, rel.r2 AS r2, null AS source
            UNION ALL
            MATCH (c:Company {isin: $isin})
            MATCH (owner)-[rel:PRODUCES|CONSUMES]->(factor:Factor)
            WHERE owner = c OR (c)-[:IN_INDUSTRY]->(owner:Industry)
            RETURN c.isin AS company_isin, c.name AS company_name,
                   factor.code AS factor_code, factor.name AS factor_name,
                   type(rel) AS relation, 'fact' AS truth,
                   null AS beta, null AS r2, rel.source AS source
        """,
            {"isin": isin, "min_r2": min_r2},
        )
        unique: dict[tuple[str, str], Exposure] = {}
        for row in rows:
            exposure = Exposure(**row)
            unique[(exposure.factor_code, exposure.relation)] = exposure
        return sorted(unique.values(), key=lambda item: (item.factor_code, item.relation))

    def resolve(self, name: str, limit: int = 5) -> list[CompanySummary]:
        """Resolve a fuzzy company name using the full-text index."""
        rows = self.client.read(
            """
            CALL db.index.fulltext.queryNodes('company_name_ft', $query)
            YIELD node AS c, score
            OPTIONAL MATCH (c)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(sector:Sector)
            OPTIONAL MATCH (c)-[:HEADQUARTERED_IN]->(country:Country)
            RETURN c.isin AS isin, c.name AS name, c.ticker AS ticker,
                   sector.name AS sector, country.code AS country,
                   c.pea_eligible AS pea_eligible, score
            ORDER BY score DESC, isin
            LIMIT $limit
        """,
            {"query": _fulltext_term(name), "limit": limit},
        )
        return [_company_summary(row) for row in rows]


class FactorService:
    """Read-only queries about commodities, currencies and rates."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def list_factors(self) -> list[FactorSummary]:
        """Return every factor ordered by kind then code."""
        rows = self.client.read(
            """
            MATCH (factor:Factor)
            RETURN factor.code AS code, factor.name AS name,
                   factor.kind AS kind, factor.unit AS unit
            ORDER BY kind, code
        """,
            {},
        )
        return [FactorSummary(**row) for row in rows]

    def exposed(self, code: str, min_r2: float = 0.1, limit: int = 20) -> list[Exposure] | None:
        """Return companies by absolute beta, or None for an unknown factor."""
        exists = self.client.read("MATCH (factor:Factor {code: $code}) RETURN factor.code AS code", {"code": code})
        if not exists:
            return None
        rows = self.client.read(
            """
            MATCH (company:Company)-[rel:SENSITIVE_TO]->(factor:Factor {code: $code})
            WHERE coalesce(rel.r2, 0.0) >= $min_r2
            RETURN company.isin AS company_isin, company.name AS company_name,
                   factor.code AS factor_code, factor.name AS factor_name,
                   'SENSITIVE_TO' AS relation, 'estimate' AS truth,
                   rel.beta AS beta, rel.r2 AS r2
            ORDER BY abs(rel.beta) DESC, company.isin
            LIMIT $limit
        """,
            {"code": code, "min_r2": min_r2, "limit": limit},
        )
        return [Exposure(**row) for row in rows]

    def resolve(self, name: str, limit: int = 5) -> list[FactorSummary]:
        """Resolve a fuzzy factor name using the full-text index."""
        rows = self.client.read(
            """
            CALL db.index.fulltext.queryNodes('factor_name_ft', $query)
            YIELD node AS factor, score
            RETURN factor.code AS code, factor.name AS name,
                   factor.kind AS kind, factor.unit AS unit, score
            ORDER BY score DESC, code
            LIMIT $limit
        """,
            {"query": _fulltext_term(name), "limit": limit},
        )
        return [FactorSummary(**row) for row in rows]
