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
