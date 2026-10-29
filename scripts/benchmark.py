"""Measure p50/p95 latency of role-B services against the benchmark database."""

import argparse
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.database.neo4j import Neo4jClient
from app.models.portfolio import PortfolioRequest, Position
from app.models.shocks import Shock, ShockRequest
from app.services.companies import CompanyService, FactorService
from app.services.portfolio import PortfolioService
from app.services.shocks import ShockService

BENCH_URI = "bolt://localhost:7689"
WARMUPS = 3


def measure(function: Callable[[], Any], iterations: int, warmups: int = WARMUPS) -> dict[str, float]:
    """Warm repeatedly, then return measured latency percentiles in milliseconds."""
    for _ in range(warmups):
        function()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep stable PROFILE evidence and identify the most informative operator."""
    operators = profile["operators"]
    preferred = next(
        (operator for operator in operators if "Index" in operator or "Scan" in operator),
        operators[0] if operators else "unknown",
    )
    return {
        "operator": preferred,
        "operators": operators,
        "db_hits": profile["db_hits"],
        "rows": profile["rows"],
        "time_ms": profile["time_ms"],
    }


def _profile(client: Neo4jClient, query: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run EXPLAIN then compact the corresponding read-only PROFILE query."""
    profile = _compact_profile(client.profile(query, params))
    profile["explain_operators"] = client.explain(query, params)
    return profile


def profile_comparisons(client: Neo4jClient) -> dict[str, Any]:
    """Measure the index, pagination and path alternatives requested for B6."""
    ticker_query = "MATCH (c:Company {ticker: $ticker}) RETURN c.isin AS isin"
    page_skip = """
        MATCH (c:Company)
        WHERE c.market_cap_eur IS NOT NULL
        RETURN c.isin AS isin, c.market_cap_eur AS cap
        ORDER BY cap DESC, isin ASC SKIP 3980 LIMIT 20
    """
    page_cursor = """
        MATCH (c:Company)
        WHERE c.market_cap_eur < $cap
           OR (c.market_cap_eur = $cap AND c.isin > $isin)
        RETURN c.isin AS isin, c.market_cap_eur AS cap
        ORDER BY cap DESC, isin ASC LIMIT 20
    """
    exposed_query = """
        MATCH (company:Company)-[rel:SENSITIVE_TO]->(factor:Factor {code: $code})
        WHERE rel.r2 >= $min_r2
        RETURN company.isin AS isin, rel.beta AS beta
        ORDER BY abs(beta) DESC, isin LIMIT 20
    """
    xray_query = """
        UNWIND $positions AS isin
        MATCH (:Fund {isin: isin})-[:TRACKS]->(:Index)-[holding:CONSTITUENT]->(company:Company)
        WITH company, sum(holding.weight) AS weight
        RETURN company.isin AS isin, weight ORDER BY weight DESC, isin
    """
    path_template = """
        MATCH (start:Company {isin: $start}), (target:Company {isin: $target})
        MATCH path = shortestPath((start)-[:CORRELATED_WITH%s]-(target))
        RETURN length(path) AS hops
    """
    evidence: dict[str, Any] = {}
    try:
        client.write("DROP INDEX company_ticker IF EXISTS", {})
        client.write("CALL db.awaitIndexes(120)", {})
        evidence["ticker_without_index"] = _profile(client, ticker_query, {"ticker": "SYN199"})
    finally:
        client.write("CREATE INDEX company_ticker IF NOT EXISTS FOR (c:Company) ON (c.ticker)", {})
        client.write("CALL db.awaitIndexes(120)", {})
    evidence["ticker_with_index"] = _profile(client, ticker_query, {"ticker": "SYN199"})

    try:
        client.write("DROP INDEX company_market_cap IF EXISTS", {})
        evidence["page_200_skip_without_index"] = _profile(client, page_skip, {})
    finally:
        client.write("CREATE INDEX company_market_cap IF NOT EXISTS FOR (c:Company) ON (c.market_cap_eur)", {})
        client.write("CALL db.awaitIndexes(120)", {})
    cursor_params = {"cap": 19_996_020_000.0, "isin": "ZZ0000039800"}
    evidence["page_200_skip_with_index"] = _profile(client, page_skip, {})
    evidence["page_200_cursor_with_index"] = _profile(client, page_cursor, cursor_params)
    try:
        client.write("DROP INDEX sensitive_r2 IF EXISTS", {})
        evidence["exposed_without_r2_index"] = _profile(client, exposed_query, {"code": "FACTOR_0", "min_r2": 0.1})
    finally:
        client.write("CREATE INDEX sensitive_r2 IF NOT EXISTS FOR ()-[rel:SENSITIVE_TO]-() ON (rel.r2)", {})
        client.write("CALL db.awaitIndexes(120)", {})
    evidence["exposed_with_r2_index"] = _profile(client, exposed_query, {"code": "FACTOR_0", "min_r2": 0.1})
    evidence["xray_10_funds"] = _profile(
        client,
        xray_query,
        {"positions": [f"ZZFUND{number:05d}0" for number in range(10)]},
    )
    path_params = {"start": "ZZ0000000000", "target": "ZZ0000001000"}
    evidence["path_bounded_6"] = _profile(client, path_template % "*..6", path_params)
    evidence["path_unbounded"] = _profile(client, path_template % "*", path_params)
    return evidence


def run(client: Neo4jClient, iterations: int) -> dict[str, Any]:
    """Benchmark every implemented role-B service entry point."""
    companies = CompanyService(client)
    factors = FactorService(client)
    portfolio = PortfolioService(client)
    shocks = ShockService(client)
    request = PortfolioRequest(
        positions=[Position(isin=f"ZZFUND{number:05d}0", amount_eur=1000) for number in range(10)]
    )
    probes: dict[str, Callable[[], Any]] = {
        "company.search": lambda: companies.search(query="Synthetic Company 199", limit=20),
        "company.get": lambda: companies.get("ZZ0000001990"),
        "company.exposures": lambda: companies.exposures("ZZ0000001990", min_r2=0.1),
        "company.resolve": lambda: companies.resolve("Synthetic Company 199", limit=5),
        "factor.list": factors.list_factors,
        "factor.exposed": lambda: factors.exposed("FACTOR_0", min_r2=0.1, limit=20),
        "factor.resolve": lambda: factors.resolve("Synthetic factor 0", limit=5),
        "portfolio.xray": lambda: portfolio.xray(request),
        "shock.simulate": lambda: shocks.simulate(
            ShockRequest(shocks=[Shock(factor_code="FACTOR_0", pct=10)], depth=1)
        ),
        "shock.path": lambda: shocks.path("Factor:FACTOR_0", "Company:ZZ0000000000", max_hops=6),
    }
    return {
        "iterations": iterations,
        "warmups": WARMUPS,
        "results": {name: measure(function, iterations) for name, function in probes.items()},
        "profiles": profile_comparisons(client),
    }


def main() -> None:
    """Run benchmarks and write a machine-readable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=BENCH_URI)
    parser.add_argument("--password", default="benchpassword")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    args = parser.parse_args()
    if args.uri != BENCH_URI:
        raise SystemExit(f"refusing to benchmark outside {BENCH_URI}")
    if args.iterations < 20:
        raise SystemExit("iterations must be at least 20")
    client = Neo4jClient(args.uri, "neo4j", args.password)
    try:
        report = run(client, args.iterations)
    finally:
        client.close()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
