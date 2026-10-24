"""Wait until a Neo4j instance answers (used by `make test-db` and CI).

Usage: python -m scripts.dev.wait_neo4j --uri bolt://localhost:7688 --password testpassword [--timeout 180]
"""

import argparse
import sys
import time

from app.database.neo4j import Neo4jClient


def main() -> None:
    """Poll the database every 3 seconds until it answers or the timeout expires."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    client = Neo4jClient(args.uri, args.user, args.password)
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            if client.ping():
                print(f"Neo4j is up at {args.uri} (GDS: {client.has_gds()})")
                return
            time.sleep(3)
    finally:
        client.close()
    sys.exit(f"Neo4j did not answer at {args.uri} within {args.timeout}s")


if __name__ == "__main__":
    main()
