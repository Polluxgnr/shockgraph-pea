# Decisions (role c)

One line per decision not settled by AGENTS.md: date, decision, why.

- 2026-10-26 · C1 · All moves in percent, rates in percentage points (`move_unit` = `pct` / `pp` on SENSITIVE_TO): so `impact_pct = beta * pct` in B3 works for every factor; a rate shock `pct` must be read as pp.
- 2026-10-26 · C1 · Two market proxies: STOXX Europe 600 (`EXSA.DE`) for European listings, MSCI World (`URTH`) for the rest: a European index does not remove the US market move from a US stock.
- 2026-10-26 · C1 · Stock returns in their listing currency, not converted to EUR: conversion would give every USD stock a mechanical EURUSD beta of about -1 that drowns the business exposure.
- 2026-10-26 · C1 · Weekly closes (Friday) resampled from daily adjusted closes, not Yahoo's `1wk` bars: same calendar for every source (FRED, ECB, all exchanges), dividends included, unfinished week dropped.
- 2026-10-26 · C1 · `r2` on SENSITIVE_TO is the **partial** r2 of the factor (t² / (t² + dof)), not the r2 of the whole regression, which the market would dominate.
- 2026-10-26 · C1 · At least 52 common weeks per regression; below that, no estimate.
- 2026-10-26 · C1 · Re-running replaces the estimates of the recomputed companies (delete `method = ols_market_controlled`, then create), so a beta that is no longer significant disappears instead of staying stale.
- 2026-10-26 · C1 · No SENSITIVE_TO on funds: a fund's exposure comes from the look-through (TRACKS → CONSTITUENT), which avoids two inconsistent numbers for the same ETF.
- 2026-10-26 · C1 · Yahoo downloads in chunks of 100 with threads, then a sequential retry of the failed symbols (yfinance's local cache raises "database is locked" under threads).
- 2026-10-26 · C1 · FRED without an API key falls back to the public CSV export, so `FRED_API_KEY` stays optional.
- 2026-10-27 · C4 · Advice requests are caught by a rule (English and French patterns) before any model call: a prompt can talk a model into a recommendation, not a regex. The small model only decides in or out of scope, and the question goes on when it fails (scope is a convenience, not a safety rule).
- 2026-10-27 · C4 · An ambiguous name stops the run with a question. Only an exact match (legal suffix aside, or the ticker) skips it: with a prefix rule, "Total" became Total Gabon instead of TotalEnergies.
- 2026-10-27 · C4 · `cypher_fallback` fails closed: if the validator raises (not merged yet) or refuses, the query never reaches Neo4j.
- 2026-10-27 · C4 · Tool errors (a service not written yet, an unknown factor) go to the trace and back to the model as `{"error": ...}`; the run continues.
- 2026-10-27 · C4 · Budget of 6 tool calls counted in the trace; calls over the budget are recorded as skipped, and the graph goes straight to the writer without another planning call.
- 2026-10-27 · C4 · Unusable writer output (bad JSON from the model) gives a fixed answer with no claim, never the planner's free text, which nothing has checked.
- 2026-10-27 · C4 · `ToolCall.result` (added, optional) keeps the full tool result: the verifier (C5) needs every number, the 300-character preview is not enough.
- 2026-10-27 · C4 · Ollama models at temperature 0, so the same question gives the same plan. The fake provider is a scripted model that plays back tool calls and structured answers.
