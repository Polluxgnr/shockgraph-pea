# Defense notes (role c)

Updated at the end of every session. See the end of docs/roles/c.md for the questions to answer.

## C1 · Betas and correlations (`scripts/ingest/prices.py`)

### What it does

1. Reads from the graph every Company with a Yahoo ticker and every Factor with a source.
2. Downloads 3 years of daily closes (Yahoo for stocks, commodities, FX and the two market proxies;
   FRED and ECB for rates), cached in `data/cache/*.parquet` for 7 days.
3. Turns them into weekly moves: returns in % for prices, changes in percentage points for rates.
4. For every (stock, factor) pair, runs one regression and writes `SENSITIVE_TO` when it is significant.
5. Removes the market from every stock, correlates what is left, and writes each stock's closest
   neighbours as `CORRELATED_WITH`.

### The regression, in words

`r_stock = a + b_m × r_market + b_f × r_factor + error`

- `b_f` is the **beta** we store: "when the factor moved 1 % (or a rate +1 pp) in a week, holding the
  market constant, the stock moved b_f % on average over the last 3 years".
- Why control for the market: oil, copper and most stocks rise and fall together with the economy.
  Without the control, almost every stock looks "sensitive to oil". The test
  `test_market_control_removes_a_spurious_beta` shows it: a stock driven only by the market gets a naive
  slope above 0.1 on the factor, and about 0 once the market is in the regression.
- Real check (26 Oct 2026): TotalEnergies vs Brent beta +0.38 (t = 11), Air France −0.48 (t = −5.8).

### Why it is an `estimate`, never a `fact`

- It is a statistic computed from the past: another window gives another number.
- It has an uncertainty (the t-stat); it can be zero in reality and look non-zero by chance.
- It is a correlation conditional on the market, not a cause: "historically moved with", never
  "because of".

### Numbers stored and what they mean

| Property | Meaning |
| --- | --- |
| `beta` | % move of the stock for a 1 % factor move (`move_unit = pct`) or +1 pp rate move (`pp`) |
| `t_stat` | beta / its standard error. \|t\| ≥ 2 ≈ less than 5 % chance to see it if the true beta were 0 |
| `r2` | **partial** r2: share of the variance left after the market that the factor explains |
| `n_obs` | number of weeks used (≥ 52) |
| `window_start`, `window_end`, `method` | the window and `ols_market_controlled` |

Partial r2 = t² / (t² + dof): the test `test_partial_r2_matches_the_two_regressions` checks it equals
(SSR without factor − SSR with factor) / SSR without factor.

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| Weekly returns | Daily | Exchanges close at different hours (Tokyo, Paris, New York): daily moves are out of sync. Weekly also halves the noise of holidays. |
| Weekly | Monthly | 3 years = 36 monthly points only, too few for a reliable regression. ~156 weekly points. |
| Changes in yield (pp) | Returns on a yield | A yield going 0.1 % → 0.2 % is a "+100 % return" that means nothing. |
| Two market proxies (Europe, World) | One world index | A European proxy leaves the US market inside a US stock's residual. |
| Local currency | Everything in EUR | EUR conversion adds a mechanical −1 EURUSD beta to every USD stock. |
| One factor at a time + market | All factors in one regression | Brent and WTI move almost identically: together, their betas become unstable (collinearity). One at a time is easier to read and to explain; the cost is that a beta can capture a close cousin factor. |
| Write when \|t\| ≥ 2 or partial r2 ≥ 0.05 | Write everything | Keeps the graph small. For ~156 weeks, partial r2 ≥ 0.05 needs \|t\| ≈ 2.8, so it is the t rule that decides; the r2 rule only matters for short histories (< ~80 weeks). |
| Replace on re-run | MERGE and update | A beta that stopped being significant must disappear. |

### Correlations (`CORRELATED_WITH`)

- On **market residuals**: raw returns of almost all European stocks correlate at 0.5+ because of the
  market. Residuals show the stocks that move together *for another reason* (same industry, same
  customers, same commodity).
- Top 5 neighbours per stock with rho ≥ 0.5; a pair chosen by either side is stored once with
  `a.isin < b.isin` and queried undirected.
- This network is the input of C2 (Louvain, PageRank, FastRP).

### Limits (say them before the jury does)

- **Multiple testing**: ~2,300 stocks × 12 factors ≈ 27,600 tests. At \|t\| ≥ 2, about 5 % of the truly
  zero betas still pass: up to ~1,400 false edges. That is why the API filters by `min_r2 = 0.1` by default
  and always shows `r2` and `t_stat`.
- Betas change over time (a regime change, a merger); we use one fixed 3-year window.
- Plain OLS standard errors: no correction for heteroskedasticity or autocorrelation (Newey-West).
- Linear: a +50 % oil shock is extrapolated from mostly small weekly moves.
- Yahoo is not an official data source; missing tickers are dropped and counted in the report.

### Cost

- Regressions: one 3-column least squares per (stock, factor), ~27,600 × 156 rows: a few seconds.
- Correlation matrix: 2,300 × 2,300 pairs on ~156 weeks: tens of seconds, in pandas, once per run.
- Writes: `UNWIND` batches of 1,000 rows, `MATCH` on the unique keys `Company.isin` and `Factor.code`
  (constraint indexes, so each lookup is an index seek, not a scan).
- The download dominates: ~2,300 tickers in chunks of 100, then the 7-day cache.

### Likely questions

- *What does a beta of −0.48 for Air France on Brent mean?* Over the last 3 years, in a week when Brent
  rose 1 % and the market was flat, Air France fell about 0.48 % on average. It is an estimate, not a
  prediction of next week.
- *Why is the r2 so low for most stocks?* Most of a stock's weekly move is the market and its own news;
  a commodity explains only a slice of what is left. A partial r2 of 0.2 is already a strong exposure.
- *Could the oil beta of an airline be caused by something else?* Yes: anything that moved with oil in
  those weeks (e.g. growth news). That is why it is labelled `estimate` and worded as "historically
  moved with", never as a cause.
- *Why not store prices in the graph?* Millions of numbers with no relationships: a time series belongs
  in a file or a time-series database; the graph stores what is derived from them.

## C6 · Demo notebook (`demo.ipynb`)

### What it does

It goes through the whole project once on the dev database, calling the same services as the API:
label and relationship counts, the look-through of a student PEA, a Brent +20 % shock on it, the GDS refresh and
its results, a drawing around TotalEnergies, the ML numbers against the baseline, and one question to the agent.

### What the numbers say (run of 28 October 2026)

- The graph: 2,466 companies, 14 funds, 12 factors; 3,579 `SENSITIVE_TO`, 3,818 `CORRELATED_WITH`,
  676 `PREDICTED_SENSITIVITY`.
- Portfolio: 7,000 EUR. The world ETF is counted through its index, so Apple, NVIDIA and Microsoft show up with
  about 220 EUR each. About 17 % of the money has a Brent estimate (`covered_weight`).
- Brent +20 %: about +90 EUR (+1.3 %), nearly all from TotalEnergies (beta about 0.39, so about +7.7 % on
  1,000 EUR). Airbus has a small negative Brent beta (-0.09) but its r2 (0.03) is below the shock's 0.1 threshold,
  and BNP Paribas has no Brent beta at all. Every line is an `estimate`; 178 more companies only get a direction, from a declared PRODUCES / CONSUMES link.
- GDS: 1,499 companies in the projection, 139 communities, modularity 0.91; the energy community has Brent, WTI
  and gas as top factors. The companies most similar to TotalEnergies (FastRP) are Eni, OMV, Galp, Equinor and
  Repsol: the method finds the oil majors without being told what they do.
- ML: AUC-PR 0.573 against 0.270 for the baseline, precision@100 of 1.00 (see the C3 section).

### Choices and rejected alternatives

- Services, not raw Cypher: the notebook shows what the API really returns. Rejected: rewriting the queries in
  the notebook, which could drift from the code.
- Outputs kept in the file: GitHub renders it, so the jury can read it without running anything.
- A real-looking portfolio rather than a single stock: the look-through is the point of a PEA tool.

### Likely questions

- *Why is the portfolio impact so small?* Most of the money is in the world ETF, and most of its companies
  have no significant Brent beta. That is the honest answer: a diversified PEA barely depends on oil.
- *Is +90 EUR what will happen?* No. It is what the past weekly co-movement implies, labelled `estimate`.
  It describes the past and does not prove causation.
- *Why does the agent cell say Ollama is not running?* The model runs locally; without it the cell explains how to
  start it instead of failing. The video shows it with the model running.
