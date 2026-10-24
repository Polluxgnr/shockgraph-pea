"""Run a `.cypher` file statement by statement.

Usage: python -m scripts.cypher_files scripts/constraints.cypher [--uri bolt://localhost:7688 --password ...]
"""

import argparse
from pathlib import Path

from app.config import get_settings
from app.database.neo4j import Neo4jClient


def split_statements(text: str) -> list[str]:
    """Split a Cypher script on `;` at the end of a line, dropping `//` comment lines and blanks."""
    statements: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def run_file(client: Neo4jClient, path: str | Path) -> int:
    """Execute every statement of the file as a write; return the number of statements run."""
    statements = split_statements(Path(path).read_text(encoding="utf-8"))
    for statement in statements:
        client.write(statement)
    return len(statements)


def main() -> None:
    """Command-line entry point."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--uri", default=settings.neo4j_uri)
    parser.add_argument("--user", default=settings.neo4j_user)
    parser.add_argument("--password", default=settings.neo4j_password)
    args = parser.parse_args()
    client = Neo4jClient(args.uri, args.user, args.password)
    try:
        for file in args.files:
            print(f"{file}: {run_file(client, file)} statements")
    finally:
        client.close()


if __name__ == "__main__":
    main()
