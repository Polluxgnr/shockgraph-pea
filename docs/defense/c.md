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

## C3 · Predicted exposures (`ml/`, `GdsService.predicted_exposures`)

### The question, in plain words

C1 gives a beta only where a stock's own prices show it clearly. The model asks something else: judging only
by **where a stock sits in the graph**, does it look like a stock exposed to this factor? One row is one
pair (company, factor), for example (Eni, NATGAS_EU). The answer is a probability, stored as
`PREDICTED_SENSITIVITY {prob, direction, model_version}` with the truth level `prediction`.

The label of a pair is "C1 would write a SENSITIVE_TO here" (|t| >= 2 or partial r2 >= 0.05): the model learns
exactly the rule the graph uses for its estimates.

### What the model sees

| Feature | Meaning |
| --- | --- |
| `produces`, `consumes` | a PRODUCES / CONSUMES link declared for the company or its industry (fact) |
| `european` | the stock trades in Europe |
| `industry_rate`, `industry_beta`, `industry_peers` | share of the *other* companies of its industry exposed to the factor, their mean beta, how many |
| `neighbour_*` | same for its CORRELATED_WITH neighbours |
| `community_*` | same for the other members of its Louvain community |
| `knn_*` | same for its 10 nearest companies by FastRP embedding (cosine) |
| `factor_*` | which factor the row is about |

Never the pair's own beta: that is the answer. Industry and community averages subtract the company itself;
the tests check it on a hand-built case.

### The temporal split (the key point)

Two windows of 3 years, one after the other: **2020-10-25 → 2023-10-24** to train, **2023-10-25 →
2026-10-23** to test. Every feature and every label is computed inside its own window, from that window's
prices only, including the correlations, the Louvain communities and the FastRP embeddings (GDS reruns on a
temporary projection of each window). The model never sees a price of the test window.

Choosing between the two models also stays in the training window: a quarter of its *companies* are held
out, and the best AUC-PR there wins. Companies, not rows: the 12 rows of one company look alike, and
splitting them would make the check too easy.

### Results (real run, 28 Oct 2026)

| | Baseline (industry share) | Logistic regression | Gradient boosting (chosen) |
| --- | --- | --- | --- |
| AUC-PR | 0.270 | 0.572 | 0.573 |
| precision@100 | 0.75 | 0.94 | 1.00 |
| precision@500 | 0.61 | 0.95 | 0.97 |

About 25,000 pairs per window; 14 % of the test pairs are exposed, which is what a random ranking would score
in AUC-PR. The models double the baseline. Of the 500 pairs the chosen model ranks highest, 97 % are
really exposed in the test window.

What it relies on (AUC-PR lost when a feature is shuffled): `neighbour_rate` 0.31, far ahead of
`industry_rate` 0.03 and `knn_rate` 0.02. In words: a stock whose correlation neighbours are exposed to a
factor is very likely exposed too.

### Why AUC-PR and precision@k, not accuracy

Only 14 % of pairs are exposed. A model that always says "not exposed" has 86 % accuracy and is useless.
AUC-PR measures how well the exposed pairs are ranked first; precision@k answers the practical question "if I
look at the k pairs the model is surest about, how many are right?".

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| Train on the older window, test on the recent one | Random split of the rows | A random split mixes the same weeks in train and test: it measures memory, not whether the rule holds in another period |
| Industry share as the baseline | No baseline | Without one, 0.57 means nothing; the industry is the first thing an analyst would look at |
| Embedding through its 10 nearest companies | 64 raw columns | A raw vector says nothing about one given factor; "are my look-alikes exposed to oil?" does |
| Gradient boosting (chosen on held-out companies) | Logistic regression | Same AUC-PR (0.573 vs 0.572), but boosting is better at the top of the list (precision@100 1.00 vs 0.94) |
| Write only prob >= 0.5 and only where no SENSITIVE_TO exists | Write every score | The graph keeps one statement per pair: an estimate when the prices show it, a prediction otherwise |
| `direction` from the peers' mean beta | A second model for the sign | The model says whether; the peers already say which way, and it stays explainable |

### Cost

- Two windows × about 28,000 regressions (3 columns, 156 weeks each): seconds.
- GDS per window: one projection of about 3,400 correlation pairs, Louvain and FastRP streamed, dropped.
- Gradient boosting on 24,000 rows: seconds. The download dominates: 6 years for about 2,400 symbols.
- Reads: `predicted_exposures` is one index seek on `Company.isin` plus its relationships, with a `LIMIT`.

### Limits (say them first)

- **Same-window features.** The neighbours' exposure and the label come from the same 3 years, and two oil
  stocks are often correlated *because* both follow oil. So part of what the model learns is "a stock that
  moves with oil stocks moves with oil". It is useful for filling gaps in the graph; it is not a forecast of
  a future exposure. A stricter test (features from the older window, labels from the recent one) would
  measure that and is the next step.
- **Noisy labels.** C1's rule lets about 5 % of truly zero betas through by chance (multiple testing), so a
  share of the "exposed" labels are noise.
- **Coverage.** Yahoo has no usable history for a few hundred tickers; a stock without prices has no
  neighbours, community or embedding, and the model is not confident about it on its industry alone.

### Likely questions

- *Is a prediction a fact?* No: truth level `prediction`, always shown with its probability and the model
  version. "The model predicts that Eni is exposed to European gas", never "Eni is exposed".
- *Why not just use the industry?* That is the baseline: 0.27 AUC-PR. Neighbours in the correlation network
  carry much more, because two companies of different industries can share an exposure (a utility and a
  chemicals company both buying gas).
- *Isn't the 0.57 inflated by the same-window features?* Partly, yes (see Limits). The split guarantees that
  nothing from the test period is used in training; it does not make the features independent of the label
  inside a window.
- *Why GDS again inside the ML code?* C2 writes communities and embeddings for the current window only. The
  older window needs its own, computed the same way, so the comparison between windows is fair.
