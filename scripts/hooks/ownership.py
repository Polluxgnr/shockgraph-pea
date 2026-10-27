"""Who owns which file. Single source of truth for the pre-commit hook and AGENTS.md.

Patterns use fnmatch (`*` also matches `/`). A file not matched by any pattern belongs to nobody:
ask the lead (role A) to add it here first.
"""

from fnmatch import fnmatch

ROLES = {"a": "Pollux (Polluxgnr)", "b": "Eliott (Tiltoe)", "c": "Saty (exilonn)"}

OWNERS: dict[str, list[str]] = {
    "a": [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude/settings.json",
        "README.md",
        "docs/roles/*",
        "docs/schema.md",
        "docs/schema.drawio",
        "docs/modeling.md",
        "docs/data_sources.md",
        "docs/guardrails.md",
        "docs/demo_checklist.md",
        "docs/img/*",
        "data/seed/*",
        "scripts/__init__.py",
        "scripts/constraints.cypher",
        "scripts/cypher_files.py",
        "scripts/seed_data.py",
        "scripts/ingest/__init__.py",
        "scripts/ingest/universe.py",
        "scripts/ingest/declared.py",
        "scripts/ingest/news.py",
        "scripts/ingest/sources/*",
        "scripts/hooks/*",
        "scripts/dev/*",
        "app/database/*",
        "app/agent/guardrails.py",
        "app/models/hypotheses.py",
        "app/services/hypotheses.py",
        "app/routers/hypotheses.py",
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/fixtures/mini_graph.cypher",
        "tests/fixtures/README.md",
        ".github/workflows/lint.yml",
        ".github/workflows/test.yml",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/*",
        ".gitignore",
        ".gitattributes",
        ".pylintrc",
        ".pre-commit-config.yaml",
        ".secrets.baseline",
        "pyproject.toml",
    ],
    "b": [
        "app/__init__.py",
        "app/main.py",
        "app/config.py",
        "app/security.py",
        "app/models/__init__.py",
        "app/models/common.py",
        "app/models/companies.py",
        "app/models/portfolio.py",
        "app/models/shocks.py",
        "app/services/__init__.py",
        "app/services/companies.py",
        "app/services/portfolio.py",
        "app/services/shocks.py",
        "app/routers/__init__.py",
        "app/routers/health.py",
        "app/routers/companies.py",
        "app/routers/portfolio.py",
        "Dockerfile",
        ".dockerignore",
        "docker-compose.yml",
        "nginx/*",
        "Makefile",
        ".github/workflows/publish.yml",
        "scripts/benchmark.py",
        "scripts/bench_data.py",
        "scripts/agent_eval.py",
        "docs/architecture.md",
        "docs/architecture.drawio",
        "docs/perf.md",
        "docs/agent_eval.md",
    ],
    "c": [
        "scripts/ingest/prices.py",
        "scripts/gds_refresh.py",
        "app/models/network.py",
        "app/models/agent.py",
        "app/services/gds.py",
        "app/routers/network.py",
        "app/routers/agent.py",
        "app/agent/__init__.py",
        "app/agent/graph.py",
        "app/agent/tools.py",
        "app/agent/verifier.py",
        "app/agent/prompts.py",
        "app/llm/*",
        "ml/*",
        "demo.ipynb",
        "tests/fixtures/*gds*.cypher",
        "docs/ml_report.md",
        "docs/agent.md",
    ],
}

# Everyone may add lines to these files; nobody may change or delete an existing line.
APPEND_ONLY = ["requirements.txt", ".env.example"]


def personal_patterns(role: str) -> list[str]:
    """Files every role owns for itself: its tests, decision log, defence notes and screenshots."""
    return [f"tests/test_{role}_*.py", f"docs/decisions/{role}.md", f"docs/defense/{role}.md", f"docs/img/{role}_*"]


def owner_of(path: str) -> str | None:
    """Return the role owning `path`, or None when no pattern matches."""
    for role, patterns in OWNERS.items():
        if any(fnmatch(path, pattern) for pattern in patterns + personal_patterns(role)):
            return role
    return None


def may_edit(role: str, path: str) -> bool:
    """True when `role` may modify `path` (append-only files are checked separately)."""
    if path in APPEND_ONLY:
        return True
    patterns = OWNERS.get(role, []) + personal_patterns(role)
    return any(fnmatch(path, pattern) for pattern in patterns)
