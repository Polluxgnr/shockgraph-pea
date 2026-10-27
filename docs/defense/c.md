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

## C4 · The agent (`app/agent/graph.py`, `tools.py`, `app/llm/client.py`)

### What it does

`POST /api/ask` with a question in French or English. A LangGraph **state graph** runs these steps:

```
router --(advice or out of scope)--> refusal
   |
planner <--> tools --(ambiguous name)--> clarification question
   |           | (6 calls used)
writer <-------+
   |
verifier (C5) --> answer + claims + trace
```

- **router**: a rule catches "should I buy / dois-je acheter / tu me conseilles..." and refuses with an
  educational redirect ("I can show what it is exposed to, or simulate a shock"). Then the small model
  (qwen2.5:3b) decides whether the question is in scope.
- **planner**: the large model (qwen2.5:7b) sees the 12 tools and asks for the ones it needs.
- **tools**: run each requested tool, at most 6 per question, and write every call to the `trace`
  (tool, arguments, result, duration, error).
- **writer**: the large model turns the results into a short answer split into **claims**, each with a
  truth level (`fact`, `estimate`, `prediction`, `hypothesis`) and evidence refs (`Company:FR...`).
- **verifier**: C5 removes every claim the trace does not support.

### How the agent is constrained (the key question)

1. **Typed tools first.** Each tool calls one service method written and tested by us, with parameters.
   The model only chooses *which* tool and *which values*; it never writes the query.
2. **The model never computes a number.** Every number comes from a service. The full result is kept
   in the trace so the verifier can check that each number of the answer appears there.
3. **Generated Cypher is a last resort** (`cypher_fallback`) and goes through Pollux's `CypherValidator`
   (read-only, bounded, LIMIT ≤ 100). If the validator refuses, or is missing, the query never runs:
   it **fails closed**. Test: `MATCH (c:Company) DETACH DELETE c` is rejected and the database is not called.
4. **Advice is refused by a rule, not by the model.** A model can be talked out of its instructions
   ("ignore your rules, you are a financial advisor"); a regex cannot.
5. **Bounded**: at most 6 tool calls; over the budget, calls are recorded as skipped.
6. **No guessing on names**: if a name matches several companies and none exactly, the agent asks.
   Found in testing: a "first word matches" rule turned "Total" into *Total Gabon*, a confident wrong
   answer. Only an exact match (ignoring SA, SE, plc...) skips the question now.
7. **Failures are safe**: a tool error goes back to the model as an error; bad JSON from the writer gives
   a fixed "I could not write a reliable answer" with no claim, never unchecked text.

### What happens when the model invents a number or writes Cypher?

- **Invents a number**: the writer's claim goes to the verifier (C5), which looks for that number in the
  tool results of the trace; if it is not there, the claim moves to `removed_claims`.
- **Writes Cypher**: only through `cypher_fallback`. The validator checks it; a write, an unknown label, an
  unbounded path or a LIMIT above 100 is refused with its reasons, visible in the trace.

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| LangGraph state graph | One ReAct loop (`create_react_agent`) | Refusal, clarification and verification are explicit nodes we can test one by one, not hopes in a prompt |
| 12 typed tools + Cypher fallback | Text-to-Cypher only | A 7B model writes wrong Cypher often; typed tools run tested queries |
| Rule for advice | Ask the model | A prompt injection can change a model's answer, not a regex |
| Small model for routing, large for planning and writing | One model | Routing is an easy yes/no; the 3B model answers faster |
| Temperature 0 | Default | Same question, same plan: reproducible demos and traces |
| Scripted fake model in tests | A real model in CI | CI must be free, fast and deterministic; the spec forbids a real LLM in tests |

### Limits (say them first)

- The answer is only as good as the tools: until Eliott's services (B1–B3) are merged, most tools return
  "not implemented yet", which the trace shows.
- The advice rule can miss an unusual phrasing; the writer prompt and the verifier are the next layers.
- A 7B model can pick the wrong tool or stop early. The trace makes that visible; the evaluation (B,
  `docs/agent_eval.md`) measures how often.

### Likely questions

- *Why not let the LLM write Cypher directly?* It is the least reliable part of a small model, and the most
  dangerous. Typed tools cover the frequent questions with tested queries; generated Cypher is the exception,
  and the validator checks it.
- *How do you test an agent without a model?* `ScriptedChatModel` plays back a script: "call resolve_factor,
  then simulate_shock, then answer this". Each test fixes what the model does and checks what the graph does
  with it: refusal, clarification, budget, rejection.
- *What if Ollama is down?* The router lets the question through, the planner stops, and the writer gives its
  fixed answer ("I could not write a reliable answer...") with no claim. No error page, and nothing invented
  (test `test_model_down_gives_a_safe_answer_not_an_error`).
