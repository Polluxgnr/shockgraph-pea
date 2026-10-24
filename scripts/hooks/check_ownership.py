"""Pre-commit hook: refuse staged changes to files owned by another role.

The role comes from `git config shockgraph.role` (set it with `make role ROLE=b`).
Skipped while resolving a merge (MERGE_HEAD exists): taking the other side of a conflict is allowed.
Emergency bypass (lead only, say it in the PR): SHOCKGRAPH_SKIP_OWNERSHIP=1 git commit ...
"""

import os
import subprocess
import sys
from pathlib import Path

from scripts.hooks.ownership import APPEND_ONLY, ROLES, may_edit, owner_of


def git(*args: str) -> str:
    """Run a git command and return its stdout."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def removed_lines(path: str) -> list[str]:
    """Lines removed from `path` in the staged diff."""
    diff = git("diff", "--cached", "-U0", "--", path)
    return [line for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]


def main() -> int:
    """Return 0 when every staged file may be committed by the current role."""
    if os.environ.get("SHOCKGRAPH_SKIP_OWNERSHIP") == "1":
        return 0
    git_dir = Path(git("rev-parse", "--git-dir").strip())
    if (git_dir / "MERGE_HEAD").exists():
        return 0
    role = git("config", "--get", "shockgraph.role").strip() if _has_role() else ""
    if role not in ROLES:
        print("No role set. Run: make role ROLE=a|b|c  (a = Pollux, b = Eliott, c = Saty)")
        return 1

    staged = [line for line in git("diff", "--cached", "--name-only", "--diff-filter=ACMRD").splitlines() if line]
    errors: list[str] = []
    for path in staged:
        if path in APPEND_ONLY:
            if removed_lines(path):
                errors.append(f"{path}: append-only, you may add lines but not change or remove them")
        elif not may_edit(role, path):
            owner = owner_of(path)
            who = f"role {owner} ({ROLES[owner]})" if owner else "nobody yet (ask role A to add it to ownership.py)"
            errors.append(f"{path}: owned by {who}")
    if errors:
        print(f"You are role {role}. These files are not yours:")
        print("\n".join(f"  - {error}" for error in errors))
        print("Unstage them (git restore --staged <file>) or ask their owner in an issue.")
        return 1
    return 0


def _has_role() -> bool:
    """True when git has a shockgraph.role value."""
    return (
        subprocess.run(["git", "config", "--get", "shockgraph.role"], capture_output=True, check=False).returncode == 0
    )


if __name__ == "__main__":
    sys.exit(main())
