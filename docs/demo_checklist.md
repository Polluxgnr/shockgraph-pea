# Grading map and video checklist

Every criterion of the grading grid, where it lives, who owns it. Tick it when it is merged in `main`.
The video walks through this table from top to bottom.

| Pts | Criterion | Where | Owner | Done |
| --- | --- | --- | --- | --- |
| 2 | docker-compose ≥ 3 services | `docker-compose.yml` (neo4j, api, nginx, ollama) | B | [x] |
| 2 | Dockerfile / official images | `Dockerfile` | B | [x] |
| 2 | `.env.example`, no secret committed | `.env.example`, hooks, detect-secrets in CI | A | [x] |
| 2 | Image on a public registry | `ghcr.io/polluxgnr/shockgraph-api` | B | [x] |
| 1 | Compose starts cleanly | `make docker-run` | B | [x] |
| 3 | ≥ 3 labels, ≥ 3 relationship types | `docs/schema.md` | A | [x] |
| 2 | ≥ 2 constraints | `scripts/constraints.cypher` | A | [x] |
| 2 | Indexes for frequent queries | `scripts/constraints.cypher`, `docs/perf.md` | A, B | [x] |
| 2 | Ingestion script | `scripts/seed_data.py`, `scripts/ingest/` | A, C | [x] |
| 1 | Sources documented | `docs/data_sources.md` | A | [x] |
| 2 | ≥ 4 endpoints, ≥ 3 advanced queries | `/api/portfolio/xray`, `/api/shocks/simulate`, `/api/paths`, `/api/network/*` | B, C | [x] |
| 2 | Pydantic validation | `app/models/` | B | [x] |
| 1 | Swagger `/docs` | http://localhost/docs | B | [x] |
| 1 | Error handling | `app/main.py`, `tests/test_b_api.py` | B | [x] |
| 4 | Pathfinding / GDS in ≥ 2 endpoints | `/api/paths` (shortestPath), `/api/network/clusters`, `/similar` | B, C | [x] |
| 3 | Complex pattern matching | x-ray look-through, shock propagation through Claims | B | [x] |
| 2 | WITH, UNWIND, subqueries, aggregations | `app/services/portfolio.py`, `shocks.py` | B | [x] |
| 5 | Optimisation + benchmarks | `docs/perf.md` | B | [x] |
| 2 | GDS projection + ≥ 2 algorithms | Louvain, PageRank, FastRP in `app/services/gds.py` | C | [x] |
| 3 | ML workflow | `ml/`, `docs/ml_report.md` | C | [x] |
| 4 | LangGraph agent with tools | `app/agent/` | C | [x] |
| 4 | LLM reliability and guardrails | `app/agent/guardrails.py`, `verifier.py`, `docs/guardrails.md`, `docs/agent_eval.md` (real-model run pending) | A, C, B | [x] |
| 2 | Graph schema diagram | `docs/schema.drawio`, `docs/img/a_schema.png` | A | [x] |
| 2 | Architecture diagram | `docs/architecture.drawio`, `docs/img/b_architecture.png` | B | [x] |
| 3 | Modeling rationale + anti-genericity | `docs/modeling.md` | A | [x] |
| 3 | README + screenshots | `README.md` (two-minute tour), `docs/img/` (demo page screenshots from the real stack pending) | A | [ ] |
| 2 | Demo notebook | `demo.ipynb` | C | [ ] |
| 2 | `.pylintrc`, pylint ≥ 9.5, `make lint` | `.pylintrc` | A | [x] |
| 1 | PEP 8, clean structure | black + pylint in CI | all | [x] |
| 1 | Docstrings | everywhere | all | [x] |
| 3 | Makefile: commands, working, documented | `Makefile`, `make help`, README | B | [x] |
| 3 | Unit / API / Neo4j tests | `tests/` | all | [x] |
| 2 | Container health tests | `tests/test_b_containers.py` | B | [x] |
| 2 | Cypher tests | `tests/test_a_skeleton.py`, `tests/test_*` on the mini graph | all | [x] |
| 3 | Coverage ≥ 60 %, all green | CI `tests` job | all | [x] |
| 3 | Lint and test workflows, badges | `.github/workflows/`, README | A | [x] |
| 1 | Code reviews (≥ 2 comments per PR) | merged PRs | all | [x] |
| 3 | Reverse proxy + authentication | `nginx/nginx.conf`, `app/security.py` | B | [x] |
| 2 | EXPLAIN / PROFILE | `docs/perf.md` | B | [x] |
| 2 | Branch workflow | protected `main`, feature branches | all | [x] |
| 1 | `.gitignore` | `.gitignore` | A | [x] |
| 6 | Balanced contributions, ≥ 1 substantial PR each | `git shortlog -sn` | all | [x] |
| 1 | Clear commit messages | commit-msg hook | all | [x] |

## Live part of the video

The script is in the two-minute tour of the README; the live parts are recorded on the GPU machine.

1. `docker compose ps`: the 4 services healthy.
2. Demo page http://localhost: x-ray of the student portfolio, `BRENT +20` shock with truth badges, the path of one
   line, the network tab, a company's side panel.
3. Neo4j browser: one ETF looked through, and the `PROFILE` of a ticker lookup.
4. Agent tab: one question with its claims, removed claims and trace; the advice question refused; the Cypher
   guardrail refusing a `UNION` without `LIMIT`.
5. GitHub: Insights → Contributors, merged PRs with review comments, Actions green, GHCR package, `v1.0` release.
