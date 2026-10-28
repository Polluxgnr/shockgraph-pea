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
