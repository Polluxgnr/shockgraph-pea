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
- 2026-10-27 · C2 · Cypher projection of the CORRELATED_WITH pairs only, not of every Company: an isolated stock would get its own community, a base PageRank and a zero FastRP embedding, which mean nothing.
- 2026-10-27 · C2 · Results of the previous refresh are removed before writing, so a stock that lost its correlations does not keep an old community or embedding.
- 2026-10-28 · C2 · (replaces the line above, after Eliott's review) The algorithms run in `mutate` mode, their results are written under staging names, and one Cypher transaction swaps them in, which also clears the companies that lost their correlations. A failure before the swap discards the staging values and leaves the previous results complete.
- 2026-10-28 · C2 · Each refresh projects `market_<id>` and refreshes are serialized by a lock in the API process: with one fixed name, two refreshes at once could drop each other's projection.
- 2026-10-27 · C2 · Louvain and FastRP with `concurrency: 1` (and FastRP `randomSeed: 42`): the same graph gives the same communities and embeddings, which the tests and the video need.
- 2026-10-27 · C2 · PageRank `maxIterations: 100` instead of 20: on the mini graph's chains and stars it did not converge in 20 (`didConverge = false`).
- 2026-10-27 · C2 · FastRP dimension 64: enough for ~1,500 projected stocks, small enough to store on every node (64 floats) for C3's features.
- 2026-10-27 · C2 · `similar` computes cosine on the written embeddings at request time, not a stored KNN graph: no extra relationships to keep fresh, one pass over ~1,500 vectors per call. Negative cosines are not returned; scores rounded to 6 decimals, ties broken by ISIN.
- 2026-10-27 · C2 · `clusters` shows at most 20 members per community (by PageRank) with the true `size`, and the 3 factors to which the most members have an estimate with r2 >= `min_r2_default`.
- 2026-10-28 · C2 · (completes the line on `market_<id>`, after Eliott's second review: the API-process lock does not cover several processes) The refresh takes a lock stored in Neo4j (`GdsRefreshLock {name: 'refresh'}` with a token, uniqueness constraint created by the refresh with `IF NOT EXISTS`); swap and discard run only in a transaction that matches the token. A second process gets `RefreshInProgress`; a lock older than 30 minutes is taken over. The lock node is deleted at the end.
- 2026-10-28 · C2 · `RefreshInProgress` is a `RuntimeError`, so the API answers 500 for now; a 409 needs a handler in `app/main.py` (not role c's file).
