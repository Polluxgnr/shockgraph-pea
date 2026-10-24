"""Full ingestion pipeline (role A orchestrates; each step belongs to its owner).

Usage: python -m scripts.seed_data [--only universe,declared,prices,news] [--skip news]

Order matters: universe (A1) -> declared exposures (A2) -> prices & betas (C1) -> news hypotheses (A3).
Every step is idempotent (MERGE on constrained keys) and exposes `run(client, settings) -> dict`.
"""

import argparse
import time

from app.config import get_settings
from app.database.neo4j import Neo4jClient
from scripts.cypher_files import run_file
from scripts.ingest import declared, news, prices, universe

STEPS = {"universe": universe.run, "declared": declared.run, "prices": prices.run, "news": news.run}


def main() -> None:
    """Apply the constraints, then run the selected steps in order and print a summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=",".join(STEPS))
    parser.add_argument("--skip", default="")
    args = parser.parse_args()
    selected = [name for name in args.only.split(",") if name and name not in args.skip.split(",")]

    settings = get_settings()
    client = Neo4jClient.from_settings(settings)
    try:
        run_file(client, "scripts/constraints.cypher")
        for name in selected:
            start = time.perf_counter()
            report = STEPS[name](client, settings)
            print(f"[{name}] {time.perf_counter() - start:.1f}s {report}")
        for row in client.read("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY label"):
            print(f"  {row['label']:<10} {row['count']}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
