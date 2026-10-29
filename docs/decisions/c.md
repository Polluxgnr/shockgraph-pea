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
- 2026-10-27 · C3 · The label is "C1 would write a SENSITIVE_TO" (|t| >= 2 or partial r2 >= 0.05), so the model predicts what the graph shows as an estimate, with the same rule.
- 2026-10-27 · C3 · Two consecutive 3-year windows, as long as C1's: the model is fitted on the older one (its features and its labels) and tested on the recent one. The choice between logistic regression and gradient boosting uses a quarter of the older window's companies, held out, never the test window.
- 2026-10-28 · C3 · The 6-year download goes in rounds (market proxies and factors alone first, then the stocks, 2 minutes between rounds, until a round brings nothing new): Yahoo refused two one-shot attempts (894, then 1,124 of 2,380 symbols, without the market proxies). No market proxy after the rounds is an error, not an empty model.
- 2026-10-27 · C3 · A pair's own regression is only its label. Industry and community features subtract the company itself; neighbours and embedding neighbours never include it.
- 2026-10-27 · C3 · Communities and embeddings are recomputed per window with GDS (Louvain and FastRP, C2's settings) on a temporary projection `ml_window` of that window's correlations, streamed and never written: the properties C2 writes only describe the current window.
- 2026-10-27 · C3 · The embedding enters through its 10 nearest companies (cosine > 0): their exposure share and mean beta for the factor. 64 raw numbers say nothing about one given factor, and the rows are (company, factor) pairs.
- 2026-10-27 · C3 · PREDICTED_SENSITIVITY is written when prob >= 0.5 and only on pairs without SENSITIVE_TO; a rerun first deletes the universe's previous predictions.
- 2026-10-27 · C3 · `direction` is the sign of the peers' mean beta (correlation neighbours, then community, embedding neighbours, industry). The model says whether a stock is exposed; its peers say which way.
- 2026-10-27 · C3 · ML prices go to their own cache, `data/cache/ml` (6 years): C1's cache holds the 3 years it was filled with.
- 2026-10-28 · C1 · A symbol Yahoo answered empty is no longer cached as unknown: under rate limiting an empty answer is a refusal, and the old rule hid ~500 stocks for 7 days. Downloads use 4 threads, a 2 s pause between chunks and a 30 s wait before the retry.
- 2026-10-27 · C3 · `Exposure` (role B) has no direction field, so `predicted_exposures` puts the model version in `source`; the direction stays on the relationship.
- 2026-10-29 · C3 · The predictions are replaced by one Cypher statement (delete, then `UNWIND` create), so one transaction: a failed row rolls everything back and the previous predictions stay. Batches in separate transactions (the first version) could delete the old links and leave half of the new ones. A staged version swapped at the end, as in C2, was rejected: the rows (676, at most one per company and factor) fit in one transaction.
- 2026-10-29 · C3 · The validation split must hold exposed and unexposed pairs on both sides. Up to 20 company splits are tried in a fixed order (same seed); none valid is an explicit error ("too few exposed companies"), not a crash inside scikit-learn or an undefined AUC-PR.
- 2026-10-29 · C3 · Two runs writing predictions at once are serialised by a lock node written first in the same transaction (`MlWriteLock`, unique `name`), and deleted at its end. Without it two runs each deleted the old links and both created theirs (duplicates in 8 of 10 tries). The Cypher uses `CALL () { ... }`: the form without scope is deprecated in Neo4j 5.26.
