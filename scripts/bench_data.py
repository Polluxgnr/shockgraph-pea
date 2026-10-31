"""Load a deterministic large synthetic graph into the dedicated benchmark database."""

import argparse
from pathlib import Path

from app.database.neo4j import Neo4jClient
from scripts.cypher_files import run_file

BENCH_URI = "bolt://localhost:7689"
COMPANIES = 20_000
SENSITIVITIES = 100_000
CORRELATIONS = 200_000
FUNDS = 50
BATCH_SIZE = 1_000


def _batches(size: int, batch_size: int = BATCH_SIZE):
    """Yield half-open integer ranges without materialising the full dataset."""
    for start in range(0, size, batch_size):
        yield start, min(start + batch_size, size)


def load(client: Neo4jClient) -> dict[str, int]:
    """Replace bench data with the contract-sized deterministic graph."""
    client.write("MATCH (node) DETACH DELETE node", {})
    run_file(client, Path(__file__).with_name("constraints.cypher"))
    client.write("CALL db.awaitIndexes(120)", {})
    client.write(
        """
        UNWIND range(0, 9) AS number
        CREATE (:Factor {code: 'FACTOR_' + toString(number), name: 'Synthetic factor ' + toString(number),
                         kind: 'commodity', unit: 'index'})
        """,
        {},
    )
    for start, stop in _batches(COMPANIES):
        client.write(
            """
            UNWIND range($start, $stop - 1) AS number
            CREATE (:Company {isin: 'ZZ' + right('000000000' + toString(number), 9) + '0',
                              name: 'Synthetic Company ' + toString(number),
                              ticker: 'SYN' + toString(number), pea_eligible: true,
                              market_cap_eur: toFloat(20000000000 - number * 1000)})
            """,
            {"start": start, "stop": stop},
        )
    for start, stop in _batches(SENSITIVITIES):
        client.write(
            """
            UNWIND range($start, $stop - 1) AS number
            MATCH (company:Company {isin: 'ZZ' + right('000000000' + toString(number % $companies), 9) + '0'})
            MATCH (factor:Factor {code: 'FACTOR_' + toString(number % 10)})
            CREATE (company)-[:SENSITIVE_TO {beta: ((number % 201) - 100) / 100.0,
                                                   r2: 0.1 + (number % 80) / 100.0}]->(factor)
            """,
            {"start": start, "stop": stop, "companies": COMPANIES},
        )
    for start, stop in _batches(CORRELATIONS):
        client.write(
            """
            UNWIND range($start, $stop - 1) AS number
            WITH number, number % $companies AS left_number,
                 (number % $companies + 1 + toInteger(number / $companies)) % $companies AS right_number
            MATCH (left:Company {isin: 'ZZ' + right('000000000' + toString(left_number), 9) + '0'})
            MATCH (right:Company {isin: 'ZZ' + right('000000000' + toString(right_number), 9) + '0'})
            CREATE (left)-[:CORRELATED_WITH {rho: 0.1 + (number % 80) / 100.0}]->(right)
            """,
            {"start": start, "stop": stop, "companies": COMPANIES},
        )
    client.write(
        """
        UNWIND range(0, $funds - 1) AS fund_number
        CREATE (fund:Fund {isin: 'ZZFUND' + right('00000' + toString(fund_number), 5) + '0',
                           name: 'Synthetic Fund ' + toString(fund_number), replication: 'physical',
                           pea_eligible: true})
        CREATE (index:Index {code: 'SYN_INDEX_' + toString(fund_number),
                             name: 'Synthetic Index ' + toString(fund_number)})
        CREATE (fund)-[:TRACKS]->(index)
        WITH fund_number, index
        UNWIND range(0, 399) AS offset
        MATCH (company:Company {isin: 'ZZ' + right('000000000' +
              toString((fund_number * 400 + offset) % $companies), 9) + '0'})
        CREATE (index)-[:CONSTITUENT {weight: 0.0025}]->(company)
        """,
        {"funds": FUNDS, "companies": COMPANIES},
    )
    counts = client.read(
        """
        MATCH (company:Company) WITH count(company) AS companies
        MATCH (:Company)-[s:SENSITIVE_TO]->() WITH companies, count(s) AS sensitivities
        MATCH (:Company)-[c:CORRELATED_WITH]->() WITH companies, sensitivities, count(c) AS correlations
        MATCH (fund:Fund) RETURN companies, sensitivities, correlations, count(fund) AS funds
        """,
        {},
    )[0]
    return counts


def main() -> None:
    """Load only a URI explicitly using the benchmark port."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=BENCH_URI)
    parser.add_argument("--password", default="benchpassword")
    args = parser.parse_args()
    if args.uri != BENCH_URI:
        raise SystemExit(f"refusing to load outside the benchmark database: expected {BENCH_URI}")
    client = Neo4jClient(args.uri, "neo4j", args.password)
    try:
        print(load(client))
    finally:
        client.close()


if __name__ == "__main__":
    main()
