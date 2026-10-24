# Role B: API, Docker, performance (Eliott, `tiltoee`)

Read `AGENTS.md` first. You own the company, factor, portfolio and shock services, the API layer, the
containers, the benchmarks and the agent evaluation. Your PRs are reviewed by **a** (`Polluxgnr`); you
review **c** (`exilonn`). Branches `feat/b<n>-…`, tests `tests/test_b_*.py`.
You never need real data: everything is tested on the mini graph (`tests/fixtures/README.md`).

## Step 0: setup (Sun 25, no PR)

1. Accept the GitHub invitation, clone, `make env install`, `make role ROLE=b`.
2. `git config user.name` / `user.email` are your GitHub identity; `gh auth status` is logged in.
3. Docker Desktop running; `make test-db`, then `make lint && make test` are green.
4. `make docker-run` once: note in `docs/decisions/b.md` what breaks (you own the fix in B5).

## Checklist (one issue = one branch = one PR, in order)

### B1 · `feat/b1-company-search` (#7, due Mon 26)
`app/services/companies.py`:
- `CompanyService.search`: full-text on `company_name_ft` when `query` is given (escape Lucene special
  characters, add a trailing `*`), filters `sector`, `pea_only`; order by `market_cap_eur` DESC then `isin`;
  **cursor pagination** (base64 JSON of the last cap and isin, `WHERE` on them), never `SKIP`.
- `get`, `exposures` (SENSITIVE_TO with `min_r2`, PRODUCES/CONSUMES at company **and** industry level, each with
  `truth`), `resolve`.
- `FactorService.list_factors`, `exposed` (|beta| DESC, `None` for an unknown code), `resolve`
  (`factor_name_ft`).
- Tests `tests/test_b_companies.py` on the mini graph, including the cursor's second page.

### B2 · `feat/b2-portfolio-xray` (#8, due Tue 27)
`PortfolioService.xray`: one query with `UNWIND $positions`, `OPTIONAL MATCH` for stock vs fund,
`Fund-[:TRACKS]->Index-[:CONSTITUENT]->Company` look-through, aggregations by company, sector, country with
`WITH` / `collect` / `sum`; factor lines = weight-averaged beta over covered companies (`r2 >= 0.1`);
unknown ISINs listed, synthetic funds warned. Exact expected numbers in the fixtures README.
Tests `tests/test_b_portfolio.py`.

### B3 · `feat/b3-shock-engine` (#9, due Wed 28)
`ShockService.simulate` and `ShockService.path`:
- Direct impact `beta × pct` (estimate) and direction-only facts; indirect impact through non-rejected
  `Claim` nodes with a `CALL {}` subquery, depth bounded by `request.depth`; with positions, the euro impact
  through the look-through of B2 (reuse it, do not copy it).
- `path`: `shortestPath` between two refs, `max_hops` validated as `int` in `[1, 6]` in Python before being
  written into the pattern (explain in your defense file why this is not an injection), each hop labelled with
  its truth level.
- Tests `tests/test_b_shocks.py`: every scenario of the fixtures README, including "Vela never appears".

### B4 · `feat/b4-api-hardening` (#10, due Wed 28)
Routes of role B: 404 / 422 / 501 / 503 with clear messages, Swagger examples (`openapi_examples` on bodies),
response descriptions, size limits. `tests/test_b_api.py`: one test per error code per route.

### B5 · `feat/b5-containers` (#11, due Thu 29)
`make docker-run` starts cleanly: Nginx on `HTTP_PORT`, API behind it (not exposed), Neo4j healthy with GDS,
Ollama healthy; `make llm-pull` works. `tests/test_b_containers.py` (marker `containers`, skipped when the stack
is down): health of Neo4j and of the API **through the proxy** (`http://localhost/api/health`), 401 on
`/api/ask` without key, 429 after the rate limit. Check `.github/workflows/publish.yml` pushes
`ghcr.io/polluxgnr/shockgraph-api` on merge to `main` (ask Pollux to make the package public: owner only).
`docs/architecture.md`: services, ports, data flows, a Mermaid diagram + `docs/architecture.drawio` and a PNG
in `docs/img/b_architecture.png`; `curl` example of every endpoint.

### B6 · `perf/b6-benchmarks` (#12, due Fri 30)
- `scripts/bench_data.py`: synthetic graph following the schema (≥ 20 000 companies, ≥ 200 000
  `CORRELATED_WITH`, ≥ 100 000 `SENSITIVE_TO`, 50 funds) **in the bench database only** (`make bench-db`,
  `bolt://localhost:7689`, password `benchpassword`).
- `scripts/benchmark.py`: p50 / p95 of every service method.
- `EXPLAIN` / `PROFILE` before/after (drop the index, profile, recreate, profile): search by name, exposed
  companies with `r2` filter, x-ray; `SKIP` vs cursor on page 200; unbounded path vs `*..6`.
- `docs/perf.md`: tables with db hits, rows, operators (`NodeIndexSeek` vs `NodeByLabelScan`), ms, and a
  screenshot of one PROFILE plan in `docs/img/b_profile.png`.

### B7 · `test/b7-agent-eval` (#13, due Fri 30)
`scripts/agent_eval.py` (`python -m scripts.agent_eval`): 30 questions on the mini graph with expected
answers (fixtures README), including 10 traps (advice request "should I buy", out of scope, ambiguous name,
write request, invented company). Run **once** with the real local model (stack up, test DB loaded);
`docs/agent_eval.md`: accuracy, removed claims, rejected Cypher, correct refusals, mean latency. Until the agent
works (C4/C5), build the harness against `ShockAgent.ask` with a fake agent.

## `docs/defense/b.md` must answer

What Pydantic validates and which HTTP code for which error; how `shortestPath` works and why the bound;
why a cursor beats `SKIP`; how to read a `PROFILE`; what the proxy and the API key protect; how the look-through
query scales with the number of positions and constituents; the numbers of `docs/perf.md`.
