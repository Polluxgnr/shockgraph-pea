"""Pre-commit hook: never commit secrets, personal agent files or generated data, even with `git add -f`."""

import re
import sys

FORBIDDEN = [
    (re.compile(r"(^|/)\.env(\..+)?$"), "secrets file"),
    (re.compile(r"(^|/)CLAUDE\.local\.md$"), "personal Claude file"),
    (re.compile(r"(^|/)AGENTS\.override\.md$"), "personal Codex file"),
    (re.compile(r"(^|/)\.claude/settings\.local\.json$"), "personal Claude settings"),
    (re.compile(r"^data/(cache|raw)/"), "generated data"),
    (re.compile(r"^ml/artifacts/"), "trained model"),
    (re.compile(r"\.(pem|key|p12|parquet|duckdb)$"), "key or data file"),
]
ALLOWED = {".env.example"}


def main() -> int:
    """Fail when one of the staged paths given by pre-commit is forbidden."""
    errors = []
    for path in sys.argv[1:]:
        normalized = path.replace("\\", "/")
        if normalized in ALLOWED:
            continue
        for pattern, reason in FORBIDDEN:
            if pattern.search(normalized):
                errors.append(f"{path}: {reason}, must not be committed")
    print("\n".join(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
