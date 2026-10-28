"""Portfolio look-through queries owned by role B."""

from app.database.neo4j import Neo4jClient
from app.models.portfolio import PortfolioRequest, XRayResult


class PortfolioService:
    """Compute economic exposure without storing the submitted portfolio."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def xray(self, request: PortfolioRequest) -> XRayResult:
        """Look through tracked indexes and aggregate all dimensions in one query."""
        positions = [position.model_dump() for position in request.positions]
        rows = self.client.read(
            """
            UNWIND $positions AS position
            OPTIONAL MATCH (stock:Company {isin: position.isin})
            OPTIONAL MATCH (fund:Fund {isin: position.isin})
            CALL {
                WITH position, stock, fund
                WITH position, stock, fund WHERE stock IS NOT NULL
                RETURN stock AS company, position.amount_eur AS amount_eur
                UNION ALL
                WITH position, stock, fund
                WITH position, stock, fund
                WHERE stock IS NULL AND fund IS NOT NULL AND $look_through
                MATCH (fund)-[:TRACKS]->(:Index)-[holding:CONSTITUENT]->(company:Company)
                RETURN company, position.amount_eur * holding.weight AS amount_eur
                UNION ALL
                WITH position, stock, fund
                WITH position, stock, fund
                WHERE stock IS NULL AND (fund IS NULL OR NOT $look_through)
                RETURN null AS company, 0.0 AS amount_eur
            }
            WITH collect({company: company, amount_eur: amount_eur}) AS expanded,
                 collect(DISTINCT CASE WHEN stock IS NULL AND fund IS NULL
                                      THEN position.isin END) AS unknown_raw,
                 collect(DISTINCT CASE WHEN fund.replication = 'synthetic'
                                      THEN fund.isin END) AS synthetic_raw,
                 $total_eur AS total_eur
            WITH expanded, total_eur,
                 [isin IN unknown_raw WHERE isin IS NOT NULL] AS unknown_isins,
                 [isin IN synthetic_raw WHERE isin IS NOT NULL |
                    'Synthetic fund ' + isin +
                    ' uses tracked index constituents; its physical HOLDS basket is ignored.'] AS warnings
            CALL {
                WITH expanded, total_eur
                UNWIND expanded AS item
                WITH item.company AS company, sum(item.amount_eur) AS amount_eur, total_eur
                WHERE company IS NOT NULL
                RETURN collect({key: company.isin, label: company.name,
                                amount_eur: amount_eur, weight: amount_eur / total_eur}) AS companies
            }
            CALL {
                WITH expanded, total_eur
                UNWIND expanded AS item
                WITH item.company AS company, item.amount_eur AS amount_eur, total_eur
                WHERE company IS NOT NULL
                MATCH (company)-[:IN_INDUSTRY]->(:Industry)-[:PART_OF]->(sector:Sector)
                WITH sector, sum(amount_eur) AS amount_eur, total_eur
                RETURN collect({key: sector.code, label: sector.name,
                                amount_eur: amount_eur, weight: amount_eur / total_eur}) AS sectors
            }
            CALL {
                WITH expanded, total_eur
                UNWIND expanded AS item
                WITH item.company AS company, item.amount_eur AS amount_eur, total_eur
                WHERE company IS NOT NULL
                MATCH (company)-[:HEADQUARTERED_IN]->(country:Country)
                WITH country, sum(amount_eur) AS amount_eur, total_eur
                RETURN collect({key: country.code, label: country.name,
                                amount_eur: amount_eur, weight: amount_eur / total_eur}) AS countries
            }
            CALL {
                WITH expanded, total_eur
                UNWIND expanded AS item
                WITH item.company AS company, item.amount_eur AS amount_eur, total_eur
                WHERE company IS NOT NULL
                MATCH (company)-[sensitivity:SENSITIVE_TO]->(factor:Factor)
                WHERE sensitivity.r2 >= 0.1
                WITH factor, total_eur,
                     sum(amount_eur * sensitivity.beta) AS weighted_sum,
                     sum(amount_eur) AS covered_amount
                RETURN collect({factor_code: factor.code,
                                weighted_beta: weighted_sum / total_eur,
                                covered_weight: covered_amount / total_eur}) AS factors
            }
            RETURN total_eur, companies, sectors, countries, factors,
                   unknown_isins, warnings
            """,
            {
                "positions": positions,
                "look_through": request.look_through,
                "total_eur": sum(position.amount_eur for position in request.positions),
            },
        )
        if not rows:
            return XRayResult(
                total_eur=sum(position.amount_eur for position in request.positions),
                companies=[],
                sectors=[],
                countries=[],
                factors=[],
            )
        row = rows[0]
        for key in ("companies", "sectors", "countries"):
            row[key] = sorted(row[key], key=lambda item: (-item["amount_eur"], item["key"]))
        row["factors"] = sorted(row["factors"], key=lambda item: item["factor_code"])
        row["unknown_isins"] = sorted(row["unknown_isins"])
        return XRayResult(**row)
