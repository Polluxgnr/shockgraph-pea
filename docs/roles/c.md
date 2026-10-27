# Role C: estimates, GDS, ML, agent (Saty, `exilonn`)

Read `AGENTS.md` first. You own the statistical estimates (betas, correlations), GDS, the ML model, the
LangGraph agent and the notebook. Your PRs are reviewed by **b** (`Tiltoe`); you review **a** (`Polluxgnr`).
Branches `feat/c<n>-…`, tests `tests/test_c_*.py`.
GDS and the agent are tested on the mini graph; betas on synthetic series; you do not wait for role A.

## Step 0: setup (Sun 25, no PR)

1. Accept the GitHub invitation, clone, `make env install`, `make role ROLE=c`.
2. `git config user.name` / `user.email` are your GitHub identity; `gh auth status` is logged in.
3. `make test-db`, then `make lint && make test` are green; `RETURN gds.version()` answers on port 7688.
4. `make docker-run && make llm-pull`: check that `qwen2.5:7b` answers with tool calls on your machine
   (latency, RAM). If too slow, try `qwen2.5:3b` as large model and write the result in `docs/decisions/c.md`.

## Checklist (one issue = one branch = one PR, in order)

### C1 · `feat/c1-betas` (#14, due Tue 27)
`scripts/ingest/prices.py`:
- Yahoo Finance (`yfinance`) weekly closes for every company with a ticker, every commodity/FX factor and a
  market proxy (e.g. iShares STOXX Europe 600 or MSCI ACWI ETF); FRED (`DGS10`, free key) and ECB Data Portal
  (`https://data-api.ecb.europa.eu/service/data/YC/...`) for rates: use **changes** in yield, not returns.
  Cache in `data/cache/*.parquet`.
- `SENSITIVE_TO`: OLS `r_stock = a + b_m · r_market + b_f · r_factor` over `returns_window_years`, keep
  `beta = b_f`, its t-stat, partial `r2`, `n_obs`, window dates, `method`. Write only |t| ≥ 2 or r2 ≥ 0.05.
- `CORRELATED_WITH`: correlation of residuals, top 5 neighbours per stock with rho ≥ 0.5, stored once
  (`a.isin < b.isin`).
- Tests `tests/test_c_betas.py` on synthetic series with a known beta (recovered within tolerance), no network.

### C2 · `feat/c2-gds` (#15, due Tue 27)
`app/services/gds.py`, `scripts/gds_refresh.py`:
- `refresh`: projection `market` (Company, CORRELATED_WITH undirected, weight `rho`) → Louvain (`community_id`),
  PageRank (`pagerank`), FastRP (`embedding`); projection dropped after use; report returned.
- `clusters` (with each community's dominant factors via SENSITIVE_TO), `systemic`, `similar` (cosine on
  embeddings, `LIMIT k`, never the company itself).
- Tests `tests/test_c_gds.py` (marker `gds`, skipped without GDS) on the mini graph: after `refresh` the three
  expected communities (pairs same/different), every company has a `pagerank`; then `REMOVE` the written
  properties so the mini graph stays intact.

### C3 · `feat/c3-ml-sensitivity` (#16, due Thu 29)
`ml/` (`python -m ml.train`), `GdsService.predicted_exposures`, `docs/ml_report.md`:
- Question: from a stock's position in the graph (industry, declared PRODUCES/CONSUMES, community, FastRP
  embedding, neighbours' betas), predict whether it is significantly exposed to a factor.
- **Temporal split**: features and labels from the older window, evaluation on the recent window (no leak).
- Logistic regression and gradient boosting vs a naive baseline (industry majority); AUC-PR and precision@k;
  report honestly even if weak.
- Write `PREDICTED_SENSITIVITY {prob, direction, model_version}` only where no estimate exists; model in
  `ml/artifacts/` (ignored). Test on the mini graph: Autoroute → EURUSD 0.71.

### C4 · `feat/c4-agent-core` (#17, due Wed 28)
`app/llm/client.py`, `app/agent/tools.py`, `app/agent/graph.py`:
- `get_chat_model`: `ollama` → `ChatOllama`; `fake` → deterministic scripted model that can emit tool calls.
- Tools, each calling **one** service method: `resolve_company`, `resolve_factor`, `company_exposures`,
  `most_exposed`, `simulate_shock`, `portfolio_xray`, `explain_path`, `clusters`, `similar_companies`,
  `predicted_exposures`, `hypotheses`, `cypher_fallback` (A's `CypherValidator` first, then `Neo4jClient.read`).
  Typed tools first; generated Cypher only as a fallback.
- LangGraph state graph: router (advice request "should I buy X" → refusal with an educational redirect;
  out of scope → refusal with reason; ambiguous name → clarification question), planner (large model, tools bound,
  ≤ 6 calls), tools, writer (structured `AgentAnswer`), then verifier (C5). Every call appended to `trace`.
- Tests `tests/test_c_agent.py` with the fake model and fake services: successful question with trace, advice
  refusal, ambiguous name, Cypher write rejected.

### C5 · `feat/c5-agent-verifier` (#18, due Thu 29)
`app/agent/verifier.py`: every evidence ref exists in the database; every number in a claim appears in a tool
result of the trace; the truth level matches the producing tool (beta → estimate, Claim → hypothesis,
PREDICTED_SENSITIVITY → prediction); causal wording on an estimate ("because", "causes", "à cause de") is
rewritten or removed. Unsupported claims move to `removed_claims`. Tests: invented number removed, wrong truth
level fixed, causal claim on a correlation removed. `docs/agent.md`: the graph of the agent (Mermaid), the tools,
and the two demo requests for the video (one success, one guardrail rejection) as `curl`.

### C6 · `docs/c6-notebook` (#19, due Fri 30)
`demo.ipynb`, run end to end with outputs (light): connection via `.env`, counts per label, a shock on a real
student-like portfolio (CW8-like ETF + a few stocks), top PageRank, communities, a subgraph drawing
(networkx + matplotlib), ML results vs baseline, one agent answer with its trace.

## `docs/defense/c.md` must answer

What an OLS beta with a market control means and why it is an estimate, not a fact; what an in-memory
projection is; why PageRank and what Louvain maximises (and its limits); how FastRP builds an embedding; why a
temporal split and what a leak is; why AUC-PR when positives are rare; how the agent is constrained (typed tools,
validator, verifier) and what happens when the model invents a number or writes Cypher; the numbers of
`docs/ml_report.md` and `docs/agent_eval.md`.
