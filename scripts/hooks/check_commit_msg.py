"""Commit-msg hook: conventional header and no tool attribution.

Header: `type(scope): summary`, at most 72 characters, e.g. `feat(api): add cursor pagination to search`.
Types: feat, fix, test, docs, refactor, perf, chore, ci, build, style. Merge and revert commits pass.
Commits are signed by their human author only: Co-Authored-By trailers or "Generated with" lines
naming an AI tool are refused.
"""

import re
import sys
from pathlib import Path

HEADER = re.compile(r"^(feat|fix|test|docs|refactor|perf|chore|ci|build|style)(\([a-z0-9._/-]+\))?!?: \S.{2,}$")
ATTRIBUTION = re.compile(
    r"(co-authored-by:.*(claude|anthropic|openai|codex|copilot|chatgpt|gpt))|generated with|🤖",
    re.IGNORECASE,
)
MAX_HEADER = 72


def check(message: str) -> list[str]:
    """Return the list of problems in a commit message (empty when it is valid)."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    header = lines[0].strip() if lines else ""
    if header.startswith(("Merge ", "Revert ")):
        return []
    problems = []
    if not HEADER.match(header):
        problems.append(f"header must look like 'feat(api): add search', got: {header!r}")
    if len(header) > MAX_HEADER:
        problems.append(f"header is {len(header)} characters, max {MAX_HEADER}")
    problems.extend(f"remove the attribution line: {line.strip()!r}" for line in lines if ATTRIBUTION.search(line))
    return problems


def main() -> int:
    """Read the message file given by git and report problems."""
    problems = check(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for problem in problems:
        print(f"commit message: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
