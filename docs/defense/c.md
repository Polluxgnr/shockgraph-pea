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

## C5 · The verifier (`app/agent/verifier.py`, `docs/agent.md`)

### What it does

The writer (the large model) turns the tool results into claims. Before anything reaches the user, the
verifier checks every claim with five rules and no model:

| Rule | Example | Outcome |
| --- | --- | --- |
| Every evidence ref exists in the database | `Company:XX0000000000` | removed |
| Every number appears in a tool result or argument | "AeroLys beta 0.85" when the tool said -0.41 | removed |
| The truth level is not stronger than its source | "Petrolia's beta is 0.62" stated as a `fact` | lowered to `estimate` |
| No causal wording on an estimate, prediction or hypothesis | "Petrolia rises *because* Brent rises" | removed |
| No buy or sell wording | "you should buy Petrolia" | removed |

Removed claims are not hidden: they go to `removed_claims` with a `reason`, so the trace shows what the model
tried to say and why it was stopped. If anything was removed, the prose is rebuilt from the claims that
passed.

### How a number is matched

- Rounding is allowed: "0.62" matches 0.6213 (within half a unit of the last digit written).
- "48 %" matches a share of 0.48; "+20 %" matches the `pct: 20` the planner passed to the shock tool.
- "falls 8.2 %" matches -8.2 (no sign written: the size is compared). "+0.41" does not match -0.41.
- `EUR10Y` and `FR0000120271` are codes, not numbers: digits stuck to letters are ignored.
- A tool call that ended in an error supports nothing.

### How the truth level is found

The truth level of a number is the one of the result it comes from. Most results say it (`truth` on an
exposure or a shock line); for the others, the field says it (`weighted_beta` is an estimate, `prob` a
prediction, `claim_id` a hypothesis). A claim with numbers takes the weakest level of its sources. A claim
without numbers may be as strong as the strongest result that names all its refs: "Petrolia produces Brent"
stays a fact even if Petrolia's Brent beta (an estimate) is also in the trace.

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| Rules | A second model as a judge | A model can be argued into agreeing; a number comparison cannot. Rules are also instant and testable |
| Lower a truth level that is too strong | Remove the claim | The statement is true, only its label is wrong: lowering keeps the information and fixes the label |
| Remove causal claims | Rewrite them ("moved with") | A rewrite by rule breaks sentences in two languages; removing is safe and the reason is shown |
| Rebuild the prose from kept claims | Keep the writer's prose | The prose is free text: a removed number could survive in it |
| Match with rounding | Exact match | The writer must be allowed to write 0.62 for 0.6213, or every answer would be emptied |

### Limits (say them first)

- A number can be supported by the wrong row: if 0.62 appears anywhere in the trace, a claim using 0.62 for
  another company passes the number rule. The truth rule looks at the right rows, the number rule does not.
- Causal and advice wording is a list of phrases (English and French). An unusual phrasing can get through.
- The verifier checks claims, not omissions: an answer that leaves out the most exposed company is not
  caught.

### Likely questions

- *What if the model invents a number?* Test `test_an_invented_number_is_removed_and_the_prose_rebuilt`:
  0.85 appears in no tool result, the claim moves to `removed_claims` with "number not found in any tool
  result: 0.85", and the prose keeps only the verified claim.
- *What if it calls a beta a fact?* It is lowered to `estimate`, with the reason "stated as fact, its source
  is a estimate: lowered" (test `test_a_beta_stated_as_a_fact_is_lowered_to_an_estimate`).
- *Why is "because" forbidden?* A beta measures how two prices moved together. Saying one *causes* the other
  is a claim the data cannot support (oil and airlines can both react to the economy).
- *Does the verifier slow the agent down?* One existence query per label (a few index seeks) and a walk
  through the trace in Python: milliseconds, next to seconds for the model.
