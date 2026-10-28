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


def measure(function: Callable[[], Any], iterations: int) -> dict[str, float]:
    """Warm once, then return measured latency percentiles in milliseconds."""
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
        "results": {name: measure(function, iterations) for name, function in probes.items()},
    }


def main() -> None:
    """Run benchmarks and write a machine-readable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=BENCH_URI)
    parser.add_argument("--password", default="benchpassword")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    args = parser.parse_args()
    if args.uri != BENCH_URI:
        raise SystemExit(f"refusing to benchmark outside {BENCH_URI}")
    if args.iterations < 5:
        raise SystemExit("iterations must be at least 5")
    client = Neo4jClient(args.uri, "neo4j", args.password)
    try:
        report = run(client, args.iterations)
    finally:
        client.close()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
