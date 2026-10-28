# Defense notes (role b)

Updated at the end of every session. See the end of docs/roles/b.md for the questions to answer.

## B1 — Company and factor search

- Full-text queries use the frozen Neo4j indexes and escape Lucene operators before adding a trailing
  wildcard. User text is always passed in `$query`, never interpolated into Cypher.
- Company lists are ordered by market cap descending then ISIN. The cursor is URL-safe base64 JSON
  containing those two values. The next query uses a range predicate, so its work does not grow with
  the page number as `SKIP` does.
- Exposure facts (`PRODUCES`, `CONSUMES`) remain distinct from statistical estimates
  (`SENSITIVE_TO`). The API never converts a direction-only fact into a numeric impact.
- Query cost is bounded by `limit + 1` for search and by the route limit for factor exposure. The extra
  search row only determines whether a next cursor exists.
- Validation run on 2026-10-28: 147 tests passed against Neo4j 5.26 + GDS; coverage 78.65%; pylint
  10.00/10. Mini-graph checks include the second cursor page and Petrolia's two BRENT truth levels.

## B2 — Portfolio x-ray

- One parameterised Cypher query receives at most 50 positions through `UNWIND`. A stock maps directly
  to itself; a fund follows `TRACKS` then `CONSTITUENT`, including synthetic funds. `HOLDS` is the
  physical substitute basket and is deliberately ignored for economic exposure.
- The query groups expanded amounts by company, sector and country with `WITH`, `sum` and `collect`.
  Factor beta is weighted by total portfolio value and only covers company estimates with `r2 >= 0.1`.
- Complexity is proportional to submitted positions plus tracked constituents, then to the sensitivity
  edges of the resulting companies. Unknown ISINs do not abort the calculation; synthetic funds produce
  an explicit warning.
- Validation run on 2026-10-28: all 140 tests on the branch passed against Neo4j 5.26 + GDS, including
  every documented fixture amount; coverage 77.59%; pylint 10.00/10.

## B3 — Shock engine

- A direct numeric impact is `beta × move` and remains an `estimate`; the response states the partial
  `r2`. A declared `PRODUCES` or `CONSUMES` edge is a `fact` and gives direction only, never a fabricated
  percentage. Supply claims propagate as `hypothesis` lines without numeric causal impact, and rejected
  claims are excluded before they can enter the result.
- Portfolio impact reuses `PortfolioService.xray`, so a synthetic fund follows its tracked index instead
  of its physical substitute basket. Complexity is the B2 look-through plus the sensitivity edges of the
  expanded companies; each indirect level queries only the current frontier and depth is capped at three.
- `shortestPath` performs a breadth-first search for the minimum-hop explanation up to `max_hops`. Cypher
  cannot parameterise a variable-length bound, so Python rejects booleans, non-integers and values outside
  `[1, 6]` before interpolating the integer. This is not an injection vector: node labels are allow-listed,
  keys and values remain query parameters, and the only formatted value has already become a bounded int.
- Each returned relationship carries its truth: sensitivities and correlations are estimates, predictions
  remain predictions, claim edges are hypotheses, and structural or declared edges are facts.
- Validation run on 2026-10-28: the 12 B3 tests passed against the real mini graph, including direct,
  indirect, rejected-claim, portfolio, unknown-factor, disconnected-path and injection-bound scenarios.

## B4 — API hardening

- Pydantic rejects malformed ISINs and evidence refs, too-short or too-long query text, more than 50
  positions, more than five simultaneous shocks, non-positive amounts, out-of-range shock moves, depths
  and `r2` values. FastAPI maps these failures to a compact 422 response that names each invalid field.
- A missing graph resource is a 404, an unfinished service is a 501, and a Neo4j driver outage is a 503.
  These contracts and descriptions are declared in OpenAPI for every role B service route. Business-body
  examples show a stock/fund x-ray and a Brent shock in Swagger.
- 404 is intentionally not used for an unknown portfolio ISIN: B2 reports it in `unknown_isins`, because
  the rest of the submitted portfolio can still be analysed safely.
- Validation run on 2026-10-28: all 33 B4 API tests passed, including 501 and 503 on each of the eight
  service routes, applicable 404 and 422 cases, collection limits and generated OpenAPI examples.

## B5 — Containers and proxy

- Nginx is the only application entry point. FastAPI has only an internal `expose: 8000`; Nginx bounds
  bodies to 64 KiB and rate-limits `/api/ask` before forwarding it. FastAPI independently protects the
  agent with a constant-time API-key comparison, so the proxy handles traffic abuse and the application
  handles authorization.
- Compose now considers Neo4j healthy only when `gds.version()` executes, and waits for both Neo4j/GDS
  and Ollama health before starting the API. The API image runs as an unprivileged UID and stores no
  credentials. Named volumes retain graph data and local models across container recreation.
- The publish workflow already pushes `latest` and the immutable commit SHA to
  `ghcr.io/polluxgnr/shockgraph-api` after a main push. Pollux must make the package public because only
  the upstream owner can change package visibility.
- Cloud validation on 2026-10-28: Compose configuration and three static container tests passed. The
  3.245 GB Ollama image downloaded successfully. The API image build could not finish because BuildKit
  could not resolve either external DNS or the cloud proxy during `pip install`, including after an
  escalated retry; consequently the three live proxy tests were skipped, not reported as passed. GDS
  installation also awaits publication of the `graphdatascience.ninja` allowlist change.
- Local follow-up validation on 2026-10-28: the complete Linux-container stack built and started with
  temporary credentials. Six tests passed through Nginx on port 8080, covering authenticated Neo4j +
  GDS health, Ollama readiness, API health, missing-key 401 and burst-rate 429. This run found and fixed
  two environment propagation defects; it does not retroactively claim that the cloud build succeeded.

## B6 — Performance

- The dedicated loader generated 20,000 companies, 100,000 sensitivities, 200,000 correlations and 50
  funds on port 7689 only. It batches writes to limit client memory.
- Fifty service measurements after three warm-ups produced the p50/p95 figures in `docs/perf.md`. Dropping
  and recreating `company_ticker` changed the lookup from `NodeByLabelScan` / 40,002 hits to
  `NodeIndexSeek` / 3 hits. EXPLAIN and PROFILE operator lists are emitted in the JSON report.
- The page-200 result is nuanced: the market-cap index reduced `SKIP` to 8,003 hits, while the current
  cursor's `OR` compiled to a `Union` costing 32,042 hits. The cursor remains stable under insertions but
  needs a range-friendly rewrite before claiming a latency advantage.
- Dropping `sensitive_r2` did not change the exposed plan or its 50,004 hits because the unique factor seek
  plus relationship expansion won the planner cost model. The report preserves this negative measurement.
- Bounded `*..6` stopped at 2,245 hits for a target beyond six hops; unbounded shortest path used 4,300.
  Server milliseconds are cache-sensitive, so plan shape and DB hits are the more defensible comparison.

## B7 — Agent evaluation and oral guide

- The suite fixes 30 questions before model execution: 20 fixture-grounded analyses and ten traps. A case
  passes only when the answer contains an accepted expectation and its refusal flag matches the contract.
- Accuracy, correct refusals, removed claims, tool errors and mean latency come from the returned
  `AgentAnswer`; the JSON preserves per-case evidence. The fake provider tests this scoring pipeline only.
  Its 30/30 result is deliberately labelled `scripted-harness-only`, never real-model accuracy.
- The real provider instantiates the frozen role C `ShockAgent` without implementing it in role B. On
  2026-10-28, `ShockAgent.ask` still raised `NotImplementedError`, so no real-model result was invented.
- Oral summary: B1 makes searches stable and traceable; B2 expands funds into economic constituents; B3
  combines statistical shocks with clearly labelled hypotheses; B4 makes HTTP failure contracts explicit;
  B5 provides a least-privilege proxied stack; B6 measures its cost on a dedicated large graph; B7 measures
  whether the natural-language layer preserves those contracts and refuses unsafe requests.
- Likely oral question — why not implement the missing agent? Ownership assigns LangGraph to role C and
  freezes its signature. B must test through a fake until C4/C5 lands, otherwise one PR would silently alter
  another contributor's responsibility.
- Likely oral question — what remains before release? Run the real provider once C4/C5 and the local model
  are available, record model identity and measured metrics, obtain Pollux's review, and merge only with
  green CI plus the required approval.
