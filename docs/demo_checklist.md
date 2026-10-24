# Grading map and video checklist

Every criterion of the grading grid, where it lives, who owns it. Tick it when it is merged in `main`.
The video walks through this table from top to bottom.

| Pts | Criterion | Where | Owner | Done |
| --- | --- | --- | --- | --- |
| 2 | docker-compose ≥ 3 services | `docker-compose.yml` (neo4j, api, nginx, ollama) | B | [ ] |
| 2 | Dockerfile / official images | `Dockerfile` | B | [ ] |
| 2 | `.env.example`, no secret committed | `.env.example`, hooks, gitleaks in CI | A | [ ] |
| 2 | Image on a public registry | `ghcr.io/polluxgnr/shockgraph-api` | B | [ ] |
| 1 | Compose starts cleanly | `make docker-run` | B | [ ] |
| 3 | ≥ 3 labels, ≥ 3 relationship types | `docs/schema.md` | A | [ ] |
| 2 | ≥ 2 constraints | `scripts/constraints.cypher` | A | [ ] |
| 2 | Indexes for frequent queries | `scripts/constraints.cypher`, `docs/perf.md` | A, B | [ ] |
| 2 | Ingestion script | `scripts/seed_data.py`, `scripts/ingest/` | A, C | [ ] |
| 1 | Sources documented | `docs/data_sources.md` | A | [ ] |
| 2 | ≥ 4 endpoints, ≥ 3 advanced queries | `/api/portfolio/xray`, `/api/shocks/simulate`, `/api/paths`, `/api/network/*` | B, C | [ ] |
| 2 | Pydantic validation | `app/models/` | B | [ ] |
| 1 | Swagger `/docs` | http://localhost/docs | B | [ ] |
| 1 | Error handling | `app/main.py`, `tests/test_b_api.py` | B | [ ] |
| 4 | Pathfinding / GDS in ≥ 2 endpoints | `/api/paths` (shortestPath), `/api/network/clusters`, `/similar` | B, C | [ ] |
| 3 | Complex pattern matching | x-ray look-through, shock propagation through Claims | B | [ ] |
| 2 | WITH, UNWIND, subqueries, aggregations | `app/services/portfolio.py`, `shocks.py` | B | [ ] |
| 5 | Optimisation + benchmarks | `docs/perf.md` | B | [ ] |
| 2 | GDS projection + ≥ 2 algorithms | Louvain, PageRank, FastRP in `app/services/gds.py` | C | [ ] |
| 3 | ML workflow | `ml/`, `docs/ml_report.md` | C | [ ] |
| 4 | LangGraph agent with tools | `app/agent/` | C | [ ] |
| 4 | LLM reliability and guardrails | `app/agent/guardrails.py`, `verifier.py`, `docs/guardrails.md`, `docs/agent_eval.md` | A, C, B | [ ] |
| 2 | Graph schema diagram | `docs/schema.drawio`, `docs/img/a_schema.png` | A | [ ] |
| 2 | Architecture diagram | `docs/architecture.drawio`, `docs/img/b_architecture.png` | B | [ ] |
| 3 | Modeling rationale + anti-genericity | `docs/modeling.md` | A | [ ] |
| 3 | README + screenshots | `README.md`, `docs/img/` | A | [ ] |
| 2 | Demo notebook | `demo.ipynb` | C | [ ] |
| 2 | `.pylintrc`, pylint ≥ 9.5, `make lint` | `.pylintrc` | A | [ ] |
| 1 | PEP 8, clean structure | black + pylint in CI | all | [ ] |
| 1 | Docstrings | everywhere | all | [ ] |
| 3 | Makefile: commands, working, documented | `Makefile`, `make help`, README | B | [ ] |
| 3 | Unit / API / Neo4j tests | `tests/` | all | [ ] |
| 2 | Container health tests | `tests/test_b_containers.py` | B | [ ] |
| 2 | Cypher tests | `tests/test_a_skeleton.py`, `tests/test_*` on the mini graph | all | [ ] |
| 3 | Coverage ≥ 60 %, all green | CI `tests` job | all | [ ] |
| 3 | Lint and test workflows, badges | `.github/workflows/`, README | A | [ ] |
| 1 | Code reviews (≥ 2 comments per PR) | merged PRs | all | [ ] |
| 3 | Reverse proxy + authentication | `nginx/nginx.conf`, `app/security.py` | B | [ ] |
| 2 | EXPLAIN / PROFILE | `docs/perf.md` | B | [ ] |
| 2 | Branch workflow | protected `main`, feature branches | all | [ ] |
| 1 | `.gitignore` | `.gitignore` | A | [ ] |
| 6 | Balanced contributions, ≥ 1 substantial PR each | `git shortlog -sn` | all | [ ] |
| 1 | Clear commit messages | commit-msg hook | all | [ ] |

## Live part of the video

1. `make help`, `make lint`, `make test`, `make docker-run` (already up), `docker compose ps`.
2. Swagger: search a company, x-ray of a student portfolio, `BRENT +20` shock with paths.
3. Neo4j browser: the schema (`CALL db.schema.visualization()`) and one shock path.
4. Agent: one successful question with its trace; one guardrail rejection (write Cypher or advice request).
5. GitHub: Insights → Contributors, merged PRs with review comments, Actions green, GHCR package.
