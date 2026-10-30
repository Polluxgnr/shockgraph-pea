"""Shock propagation and path explanation (role B). Signatures are frozen."""

from typing import Any

from app.database.neo4j import Neo4jClient
from app.models.portfolio import PortfolioRequest
from app.models.shocks import PathResult, ShockRequest, ShockResult
from app.services.portfolio import PortfolioService

MAX_PATH_HOPS = 6

RELATION_TRUTHS = {
    "SENSITIVE_TO": "estimate",
    "CORRELATED_WITH": "estimate",
    "PREDICTED_SENSITIVITY": "prediction",
    "SUBJECT": "hypothesis",
    "OBJECT": "hypothesis",
    "EXTRACTED_FROM": "hypothesis",
}


class ShockService:
    """Simulates factor shocks on companies and portfolios."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def simulate(self, request: ShockRequest) -> ShockResult:
        """Propagate the shocks.

        Direct: `impact_pct = beta * pct` for each SENSITIVE_TO with r2 >= min_r2 (truth `estimate`);
        PRODUCES/CONSUMES add a direction without a number (truth `fact`).
        Indirect (depth >= 2): companies linked to a hit company through a Claim (SUPPLIES), truth
        `hypothesis`, no number. With positions, the impact is aggregated in euros (look-through).
        Unknown factor codes raise KeyError (the router answers 404).
        """
        factor_codes = [shock.factor_code.upper() for shock in request.shocks]
        factor_rows = self.client.read(
            """
            UNWIND $factor_codes AS factor_code
            OPTIONAL MATCH (factor:Factor {code: factor_code})
            WITH factor_code, count(factor) AS matches
            RETURN [code IN collect(CASE WHEN matches = 0 THEN factor_code END)
                    WHERE code IS NOT NULL] AS missing
            """,
            {"factor_codes": factor_codes},
        )
        missing = factor_rows[0]["missing"] if factor_rows else factor_codes
        if missing:
            raise KeyError(missing[0])
        direct_rows = self.client.read(
            """
            UNWIND $shocks AS shock
            MATCH (factor:Factor {code: shock.factor_code})
            WITH collect({shock: shock, factor: factor}) AS resolved
            CALL {
                WITH resolved
                UNWIND resolved AS item
                WITH item.shock AS shock, item.factor AS factor
                WHERE factor IS NOT NULL
                MATCH (company:Company)-[sensitivity:SENSITIVE_TO]->(factor)
                WHERE sensitivity.r2 >= $min_r2
                RETURN company, factor, shock.pct AS pct,
                       sensitivity.beta * shock.pct AS impact_pct,
                       sensitivity.r2 AS r2, 'estimate' AS truth,
                       'historical sensitivity' AS reason
                UNION ALL
                WITH resolved
                UNWIND resolved AS item
                WITH item.shock AS shock, item.factor AS factor
                WHERE factor IS NOT NULL
                MATCH (company:Company)-[:IN_INDUSTRY]->(industry:Industry)
                MATCH (source)-[declared:PRODUCES|CONSUMES]->(factor)
                WHERE (source = company OR source = industry)
                AND NOT EXISTS {
                    MATCH (company)-[sensitivity:SENSITIVE_TO]->(factor)
                    WHERE sensitivity.r2 >= $min_r2
                }
                RETURN DISTINCT company, factor, shock.pct AS pct,
                       null AS impact_pct, null AS r2, 'fact' AS truth,
                       CASE type(declared)
                           WHEN 'PRODUCES' THEN 'declared producer'
                           ELSE 'declared consumer'
                       END AS reason
            }
            RETURN company.isin AS isin, company.name AS name,
                   factor.code AS factor_code, pct, impact_pct, r2, truth, reason
            ORDER BY factor_code, impact_pct DESC, isin
            """,
            {
                "shocks": [
                    {"factor_code": code, "pct": shock.pct}
                    for code, shock in zip(factor_codes, request.shocks, strict=True)
                ],
                "min_r2": request.min_r2,
            },
        )
        direct = []
        direct_isins: set[str] = set()
        frontier: dict[str, list[str]] = {}
        for row in direct_rows:
            if row.get("isin") is None:
                continue
            direct_isins.add(row["isin"])
            path = [f"Factor:{row['factor_code']}", f"Company:{row['isin']}"]
            frontier[row["isin"]] = path
            direct.append(
                {
                    "isin": row["isin"],
                    "name": row["name"],
                    "truth": row["truth"],
                    "impact_pct": row["impact_pct"],
                    "path": path,
                    "explanation": self._direct_explanation(row),
                }
            )

        indirect = self._indirect_impacts(frontier, direct_isins, request.depth)
        portfolio_impact_eur = None
        portfolio_impact_pct = None
        warnings: list[str] = []
        if request.positions:
            xray = PortfolioService(self.client).xray(PortfolioRequest(positions=request.positions))
            amounts = {line.key: line.amount_eur for line in xray.companies}
            portfolio_impact_eur = sum(
                amounts.get(line["isin"], 0.0) * line["impact_pct"] / 100
                for line in direct
                if line["impact_pct"] is not None
            )
            portfolio_impact_pct = portfolio_impact_eur / xray.total_eur * 100
            warnings.extend(xray.warnings)
            warnings.extend(f"Unknown ISIN {isin} was ignored." for isin in xray.unknown_isins)
            for line in direct:
                if line["impact_pct"] is not None and line["isin"] in amounts:
                    line["impact_eur"] = amounts[line["isin"]] * line["impact_pct"] / 100

        return ShockResult(
            direct=direct,
            indirect=indirect,
            portfolio_impact_eur=portfolio_impact_eur,
            portfolio_impact_pct=portfolio_impact_pct,
            warnings=warnings,
        )

    def path(self, from_ref: str, to_ref: str, max_hops: int = 4) -> PathResult | None:
        """Shortest explainable path between two refs (`Label:key`), or None if there is none.

        `max_hops` cannot be a Cypher parameter in a variable-length pattern: validate it as an int in
        [1, MAX_PATH_HOPS] in Python before inserting it in the query.
        """
        if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= MAX_PATH_HOPS:
            raise ValueError(f"max_hops must be an int in [1, {MAX_PATH_HOPS}]")
        from_label, from_key = self._parse_ref(from_ref)
        to_label, to_key = self._parse_ref(to_ref)
        # Cypher cannot parameterise a relationship bound. Only the validated integer above is interpolated.
        query = f"""
            MATCH (start), (target)
            WHERE $from_label IN labels(start) AND start[$from_property] = $from_key
              AND $to_label IN labels(target) AND target[$to_property] = $to_key
            MATCH path = shortestPath((start)-[*..{max_hops}]-(target))
            WHERE none(node IN nodes(path) WHERE node:Claim AND node.status = 'rejected')
            RETURN [node IN nodes(path) |
                CASE
                    WHEN node:Company THEN 'Company:' + node.isin
                    WHEN node:Fund THEN 'Fund:' + node.isin
                    WHEN node:Index THEN 'Index:' + node.code
                    WHEN node:Sector THEN 'Sector:' + node.code
                    WHEN node:Industry THEN 'Industry:' + node.code
                    WHEN node:Country THEN 'Country:' + node.code
                    WHEN node:Factor THEN 'Factor:' + node.code
                    WHEN node:Claim THEN 'Claim:' + node.claim_id
                    WHEN node:Article THEN 'Article:' + node.url
                END] AS nodes,
                [relation IN relationships(path) | type(relation)] AS relations
            LIMIT 1
        """
        rows = self.client.read(
            query,
            {
                "from_label": from_label,
                "from_property": self._key_property(from_label),
                "from_key": from_key,
                "to_label": to_label,
                "to_property": self._key_property(to_label),
                "to_key": to_key,
            },
        )
        if not rows:
            return None
        row = rows[0]
        return PathResult(
            from_ref=from_ref,
            to_ref=to_ref,
            hops=len(row["relations"]),
            nodes=row["nodes"],
            relations=row["relations"],
            truth_levels=[RELATION_TRUTHS.get(relation, "fact") for relation in row["relations"]],
        )

    def _indirect_impacts(
        self, frontier: dict[str, list[str]], direct_isins: set[str], depth: int
    ) -> list[dict[str, Any]]:
        """Expand non-rejected supply claims up to the requested company depth."""
        discovered = set(direct_isins)
        indirect: dict[str, dict[str, Any]] = {}
        for _ in range(1, depth):
            if not frontier:
                break
            rows = self.client.read(
                """
                UNWIND $sources AS source_isin
                MATCH (source:Company {isin: source_isin})
                CALL {
                    WITH source
                    MATCH (claim:Claim)-[:SUBJECT]->(source)
                    MATCH (claim)-[:OBJECT]->(target:Company)
                    WHERE claim.status <> 'rejected'
                    RETURN claim, target
                }
                RETURN source.isin AS source_isin, claim.claim_id AS claim_id,
                       claim.confidence AS confidence, target.isin AS isin, target.name AS name
                ORDER BY confidence DESC, claim_id, isin
                """,
                {"sources": sorted(frontier)},
            )
            next_frontier: dict[str, list[str]] = {}
            for row in rows:
                if row["isin"] in discovered:
                    continue
                path = frontier[row["source_isin"]] + [f"Claim:{row['claim_id']}", f"Company:{row['isin']}"]
                discovered.add(row["isin"])
                next_frontier[row["isin"]] = path
                indirect[row["isin"]] = {
                    "isin": row["isin"],
                    "name": row["name"],
                    "truth": "hypothesis",
                    "path": path,
                    "explanation": (
                        f"A non-rejected supply claim (confidence {row['confidence']:.2f}) "
                        "suggests an indirect exposure; no causal percentage is asserted."
                    ),
                }
            frontier = next_frontier
        return list(indirect.values())

    @staticmethod
    def _direct_explanation(row: dict[str, Any]) -> str:
        """Describe a direct line without mixing truth levels."""
        if row["truth"] == "estimate":
            return (
                f"Historical beta estimate with r2={row['r2']:.2f}: "
                f"{row['factor_code']} {row['pct']:+g}% implies {row['impact_pct']:+g}%."
            )
        direction = "benefits from" if row["reason"] == "declared producer" else "is exposed to"
        return f"Declared fact: the company {direction} {row['factor_code']}; direction only."

    @staticmethod
    def _parse_ref(reference: str) -> tuple[str, str]:
        """Split and validate an evidence reference."""
        if not isinstance(reference, str) or ":" not in reference:
            raise ValueError("references must use Label:key")
        label, key = reference.split(":", 1)
        if label not in {"Company", "Fund", "Index", "Sector", "Industry", "Country", "Factor", "Claim", "Article"}:
            raise ValueError(f"unsupported reference label: {label}")
        if not key:
            raise ValueError("reference key cannot be empty")
        return label, key

    @staticmethod
    def _key_property(label: str) -> str:
        """Return the schema key property for an allowed node label."""
        if label in {"Company", "Fund"}:
            return "isin"
        return {
            "Index": "code",
            "Sector": "code",
            "Industry": "code",
            "Country": "code",
            "Factor": "code",
            "Claim": "claim_id",
            "Article": "url",
        }[label]
