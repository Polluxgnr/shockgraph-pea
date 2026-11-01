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

## C2 · Graph algorithms (`app/services/gds.py`)

### What it does

`POST /api/network/refresh` (API key) or `make gds`:

1. Builds an **in-memory projection** `market_<id>` (a new name for each run): every pair of stocks linked by
   `CORRELATED_WITH`, undirected, weighted by `rho`.
2. Runs **Louvain** → `community_id`, **PageRank** → `pagerank`, **FastRP** → `embedding` (64 numbers) in
   `mutate` mode: the results stay in GDS memory.
3. Writes the three results to the Company nodes under staging names (`community_id_next`...).
4. Swaps them in with **one Cypher transaction**: every company takes its new values, or loses its old ones
   if it has no correlation any more.
5. Drops the projection. If anything failed before step 4, the staging values are deleted and the previous
   results are still there, complete.

Two refreshes at the same time (found in Eliott's reviews): each has its own projection name, so one cannot
drop the other's. The staging properties are shared, though, so a refresh first takes a **lock stored in
Neo4j**: a `GdsRefreshLock` node holding its token. Every process sees it (two API workers, `make gds` during
an HTTP call), and a second refresh gets `RefreshInProgress` instead of mixing its values in. The swap and the
discard only run in a transaction that still matches the token, so a refresh that lost its lock can neither
publish nor erase anything. A lock older than 30 minutes was left by a crashed process and is taken over. Inside
one process, a Python lock makes refreshes wait for each other instead of being refused.

The read endpoints (`/api/network/clusters`, `/api/network/systemic`, `/api/companies/{isin}/similar`) only read
those properties: they never need GDS, and they keep working when no projection exists.

### What is an in-memory projection?

GDS algorithms do not run on the database itself. GDS first copies the part of the graph the algorithm needs
(here: stocks and their correlations) into a compact in-memory structure, the **projection**. Algorithms run fast
there, then write results back as node properties. The projection uses RAM, so it is dropped after use.

Why only correlated stocks: an isolated stock would be its own community, get a base PageRank and a zero
embedding. Projecting it adds noise, not information.

### Louvain: what does it maximise?

**Modularity**: "are there more links inside the groups than you would expect by chance, given how connected each
stock is?". Louvain starts with every stock alone, moves each stock to the neighbouring group that increases
modularity the most, then merges each group into one node and repeats, until nothing improves.

On our graph a community = **stocks that move together after removing the market**: same industry, same
commodity, same customers. `clusters` shows, for each group, the factors to which most members are exposed.

Limits:
- **Resolution limit**: on a big graph, Louvain can merge small genuine groups into a larger one.
- Results depend on node order: we use `concurrency: 1` for reproducible runs.
- It is a description of past co-movement (estimate), not a classification of the companies.

### PageRank: why, and what "systemic" means here

PageRank gives a high score to a stock linked to many stocks that are themselves well linked. On the residual
correlation graph, a high PageRank = a stock at the centre of a dense web of co-movements: a shock that hits it
is likely to show up in many others. That is what `/api/network/systemic` ranks.

- Weighted by `rho`: a strong correlation counts more.
- Damping 0.85 (default): the score is the probability that a random walk is on that stock, restarting
  anywhere 15 % of the time.
- `maxIterations: 100`: with the default 20 it did not converge on small chains (`didConverge = false` in the
  report), because the error only shrinks by about 0.85 per iteration.

Why not degree (number of neighbours)? Degree treats all neighbours the same; PageRank counts a link to a hub
more than a link to a stock on the edge of the graph.

### FastRP: how does it build an embedding?

1. Gives every stock a random sparse vector of 64 numbers (fixed by `randomSeed: 42`).
2. Each stock's vector becomes the weighted average of its **neighbours'** vectors (1 hop), then of its
   neighbours' neighbours (2 hops).
3. The embedding is the sum of those two steps (weights 0, 1, 1: the stock's own random vector is not kept).

Result: two stocks with similar neighbourhoods get similar vectors, even if they are not directly linked.
`similar` compares vectors with **cosine similarity** (1 = same direction, 0 = unrelated).

It is a *random projection*, not a trained model: fast (one pass per iteration), no learning, but the numbers
themselves mean nothing alone; only comparisons do. C3 uses these vectors as ML features.

Found in the tests: in a star of 3 stocks (Siliqa linked to Varenne and Vela), all three see exactly the same
stocks within 2 hops, so they get **identical** embeddings (cosine 1). Varenne's closest companies are therefore
a tie between Siliqa and Vela, broken by ISIN. A good question to expect: FastRP finds "same position in the
network", not "same company profile".

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| Louvain | Label propagation | Faster but unstable from run to run; Louvain optimises an explicit score (modularity) we can report |
| Louvain | Weakly connected components | Components only split what is not linked at all; they cannot cut a connected group into finer groups |
| PageRank | Betweenness | Betweenness measures "bridge" stocks and costs O(n·m); PageRank measures influence in dense areas, in near-linear time |
| FastRP | Node2Vec | Node2Vec trains a neural model (slow, random walks); FastRP is a few matrix products |
| Cosine at request time | KNN written as `SIMILAR_TO` | No extra relationships to keep fresh; ~1,500 vectors per call is cheap |
| Drop the projection | Keep it in memory | RAM; and a stale projection would give results that no longer match the graph |
| `mutate`, stage, then swap in one transaction | `write` mode, one property after the other | With `write`, a crash after Louvain left companies with a new community and no embedding, and the old results were already gone (Eliott's review). The swap is all or nothing |
| A projection name per run | One fixed name `market` | Two refreshes at once could drop each other's projection |
| A lock node in Neo4j, swap and discard tied to its token | A Python `threading.Lock` only | The Python lock only covers one process. With 4 processes refreshing at once, the old code sometimes ended with 0 communities, 0 PageRanks and 0 embeddings: one process swapped after another had already published and cleared the staging |
| Refuse the second refresh | Make it wait for the lock | Waiting across processes needs polling and a timeout; refusing is immediate and the caller can retry. Inside one process the Python lock still makes them wait |
| Per-run staging property names (`community_id_<id>`) | One lock | Neo4j cannot take a property name as a parameter, so the name would have to be written into the Cypher string, which the project rules forbid |

### Cost

- Projection: one pass over the `CORRELATED_WITH` relationships (≈ 5 per stock, so a few thousand).
- Louvain: about O(m) per pass, a few passes. PageRank: O(m) per iteration, ≤ 100 iterations.
  FastRP: O(m × 64) per iteration, 2 iterations. On ~1,500 nodes and a few thousand edges: well under a second.
- Mini graph: the whole refresh takes about 0.5 s once Neo4j is warm (about 3 s the very first time).
- `similar`: one pass over the companies with an embedding (n × 64 multiplications). `clusters` and `systemic`
  sort at most ~1,500 nodes; a `LIMIT` is on every list.

### Likely questions

- *Why run the algorithms on correlations and not on the supply-chain claims?* Correlations exist for every
  priced stock and are numbers; claims are few hypotheses. Mixing them would put a news rumour in a fact-like score.
- *Is a Louvain community a sector?* No: it is a group that moved together over 3 years once the market is
  removed. It often matches an industry, sometimes not (two industries exposed to the same commodity), and
  comparing both is part of C3.
- *What happens when you rerun?* Same graph, same results (`concurrency: 1`, `randomSeed`). New betas from C1:
  the new results replace the old ones in one transaction, at the very end.
- *What if two API workers refresh at the same time?* Only one holds the lock in Neo4j, the others get
  `RefreshInProgress` (the API answers 409 Conflict: retry later). `test_refreshes_from_four_processes_at_once_never_mix` starts 4 real processes at the same
  instant: one succeeds, three are refused, and the graph ends complete. The same test sometimes failed on the old
  code, with every result erased.
- *Why a lock node and not a Neo4j built-in lock?* Neo4j only locks nodes during a transaction, and a refresh is
  several transactions (projection, algorithms, swap). The node keeps the lock across them; the uniqueness
  constraint makes sure two processes cannot create two lock nodes, and writing to the node before reading it
  makes a takeover safe.
- *And if the database crashes in the middle?* Before the swap, nothing visible has changed: the test
  `test_a_failure_after_louvain_keeps_the_previous_results` makes PageRank fail after Louvain and checks that
  the previous communities and embeddings are untouched, with no staging value and no projection left.

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
| Replace the predictions in one statement (one transaction) | Delete, then write in batches | Batches are separate transactions: a crash in the middle deleted the old predictions and left a part of the new ones. Now a failure keeps the old ones (a test breaks the second row on purpose) |
| Validation split checked for both classes, up to 20 tries | The first random split | With few exposed companies, the held-out quarter can hold none of them: AUC-PR is then undefined. Same seed, so the same data gives the same split |

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
- *And two runs at the same time?* The query writes a lock node first (`MlWriteLock`, unique name). The second
  run waits on it until the first commits, then deletes what the first wrote: one set of links, never both. Without
  it, 3 runs at once left duplicates in 8 rounds out of 10. The lock is deleted at the end of the same transaction.
- *What if the write crashes halfway?* Nothing changes. The delete and the creates are one Cypher statement, so
  one transaction: Neo4j applies all of it or none of it. The first version used batches and could leave the
  graph with half of the new predictions; Eliott caught it in review.
- *Why GDS again inside the ML code?* C2 writes communities and embeddings for the current window only. The
  older window needs its own, computed the same way, so the comparison between windows is fair.

## C4 · The agent (`app/agent/graph.py`, `tools.py`, `app/llm/client.py`)

### What it does

`POST /api/ask` with a question in French or English. A LangGraph **state graph** runs these steps:

```
router --(advice or write request)--> refusal
   |
planner --(no tool at its first step)--> refusal (out of scope)
   |  <--> tools --(ambiguous name)--> clarification question
   |           | (6 calls used)
writer <-------+
   |
verifier (C5) --> answer + claims + trace
```

- **router**: a rule catches "should I buy / dois-je acheter / tu me conseilles..." and refuses with an
  educational redirect ("I can show what it is exposed to, or simulate a shock"). A second rule refuses orders
  to change the graph ("Delete the claim", "Crée un nœud"): it is read-only.
- **planner**: the large model (qwen2.5:7b) sees the 12 tools and asks for the ones it needs. If it replies
  without any tool at its first step, the question is out of scope and gets the refusal. (Until 1 Nov a small
  model decided scope before the planner: it refused 13 of 20 ordinary questions, see below.)
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
| Scope decided by the planner (no tool fits = out of scope) | A small-model router (qwen2.5:3b) before the planner | Measured on the 30 evaluation questions: the 3B router was wrong on 14, still 6 after a rewritten prompt, and 18 with a one-word verdict; the 7B as router 11. Both tried to guess whether a company exists, which only the tools know. The planner was wrong on 0 of 26, and normal questions save a model call |
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
- *How good is the agent with the real model?* `python -m scripts.agent_eval --provider real` (Eliott's 30 questions
  on the mini graph, qwen2.5:7b on a CPU): 5 of 30 on 31 Oct, 14 of 30 after the router fix of 1 Nov. The first
  run found the router bug (13 of 20 ordinary questions refused). What still fails, read from the traces: the 7B
  sometimes skips `resolve_company` and copies the example ISIN of the tool descriptions (TotalEnergies), the
  writer once wrote an evidence ref without its label (`FRTEST000029`), and "EUR USD" does not resolve to
  EURUSD. Each wrong answer became "I could not verify any statement", never an invented number.
- *What if Ollama is down?* The rules still refuse advice and writes, the planner stops, and the writer gives its
  fixed answer ("I could not write a reliable answer...") with no claim. No error page, and nothing invented
  (test `test_model_down_gives_a_safe_answer_not_an_error`).

## C5 · The verifier (`app/agent/verifier.py`, `docs/agent.md`)

### What it does

The writer (the large model) turns the tool results into claims. Before anything reaches the user, the
verifier checks every claim with five rules and no model:

| Rule | Example | Outcome |
| --- | --- | --- |
| Every evidence ref exists in the database | `Company:XX0000000000` | removed |
| Every number appears in a tool result or argument | "AeroLys beta 0.85" when the tool said -0.41 | removed |
| ... in a result about the claim's evidence | "AeroLys beta 0.62" when 0.62 is Petrolia's beta | removed |
| A non-refused answer has at least one claim | prose with numbers, `claims` empty | safe answer |
| The truth level is not stronger than its source | "Petrolia's beta is 0.62" stated as a `fact` | lowered to `estimate` |
| No causal wording on an estimate, prediction or hypothesis | "Petrolia rises *because* Brent rises" | removed |
| No buy or sell wording | "you should buy Petrolia", "vous devriez vendre" | removed |
| Some tool result is about the claim's evidence | "AeroLys produces copper" when no result names AeroLys and copper | removed |

Removed claims are not hidden: they go to `removed_claims` with a `reason`, so the trace shows what the model
tried to say and why it was stopped. The answer shown is always the claims that passed, joined: the writer's
own prose cannot be tied to evidence, so it never reaches the user.

### How a number is matched

- Rounding is allowed: "0.62" matches 0.6213 (within half a unit of the last digit written).
- "48 %" matches a share of 0.48; "+20 %" matches the `pct: 20` the planner passed to the shock tool.
- "falls 8.2 %" matches -8.2 (no sign written: the size is compared). "+0.41" does not match -0.41.
- `EUR10Y` and `FR0000120271` are codes, not numbers: digits stuck to letters are ignored.
- "1,000" is read both ways (a thousand, or 1.000 with a French comma); it is supported if either reading
  matches. Same for "1 000" with a no-break space.
- A tool call that ended in an error supports nothing.
- The number must come from the right row. A row that names companies (`isin`, `company_isin`, ...) only
  supports a claim citing one of them. A row without a company (a portfolio's factor line) must share a
  code with the evidence (`factor_code`, `claim_id`, or what the call was asked about). The arguments
  ("+20 %") and the length of a returned list support any claim: they are the question, not a result.
- No claim at all (the writer left them out) means nothing was checked: the answer becomes "I could not
  verify any statement...". The clarification question never goes through the verifier.

### How the truth level is found

The truth level of a number is the one of the result it comes from. Most results say it (`truth` on an
exposure or a shock line); for the others, the field says it (`weighted_beta` is an estimate, `prob` a
prediction, `claim_id` a hypothesis). A claim with numbers takes the weakest level of its sources. A claim
without numbers may be as strong as the strongest result that names all its refs: "Petrolia produces Brent"
stays a fact even if Petrolia's Brent beta (an estimate) is also in the trace.

### Choices and rejected alternatives

| Choice | Rejected | Why |
| --- | --- | --- |
| Rules for refs, numbers and truth levels | A second model as a judge | A model can be argued into agreeing; a number comparison cannot. Rules are also instant and testable |
| A model judge for advice, on top of the phrases | Keep growing the phrase list | Measured on 40 labelled sentences: the phrases catch 15 % of the advice, the qwen2.5:7b judge 100 %. Advice is a meaning ("a good fit for your PEA"), not a word. This is how guardrail tools do it (Llama Guard, NeMo self-check, Coralogix policies) |
| The 7B as judge | The 3B (faster) | The 3B caught 85 % and flagged 2 of 10 plain impact claims as advice, including the TotalEnergies one shown in the demo; the 7B none, and it is already loaded |
| The judge can only remove | Let it rewrite or restore | It sees one sentence, not the conversation, and its SAFE never brings back a claim the rules removed |
| No verdict removes the claim (fail closed) | Let the phrases decide (fail open) | The phrases catch 15 % of the advice: with Ollama down, "a good fit for your PEA" would be shown. Eliott caught it in review |
| No judge with the fake provider | The scripted model as judge | It never answers ADVICE or SAFE, so failing closed would empty every answer in the tests and in the evaluation |
| Lower a truth level that is too strong | Remove the claim | The statement is true, only its label is wrong: lowering keeps the information and fixes the label |
| Remove causal claims | Rewrite them ("moved with") | A rewrite by rule breaks sentences in two languages; removing is safe and the reason is shown |
| Always show the kept claims, not the prose | Keep the writer's prose when nothing was removed | The prose cannot be tied to refs: it could give Petrolia's 0.62 to AeroLys while every claim is right |
| Match with rounding | Exact match | The writer must be allowed to write 0.62 for 0.6213, or every answer would be emptied |
| Tie a number to the row that holds it | Any number of the trace | Petrolia's 0.62 would support "AeroLys beta 0.62" |
| No claims: replace with the safe answer | Ask the writer again | A retry costs another model call and may fail the same way; the safe answer is immediate and honest |

### Limits (say them first)

- A row is matched by its ISIN and codes, not by the meaning of the field: a claim calling Petrolia's r2 its
  beta (both on the same row) passes. The first version was weaker: any 0.62 in the trace supported any
  claim; Eliott found it in review and the number is now tied to its row.
- Causal wording is still a list of phrases (English and French): an unusual phrasing can get through.
- The advice judge is measured on 40 sentences we wrote ourselves: 100 % is on that set, not a guarantee.
  It also removed one description ("Je ne peux pas vous dire s'il faut acheter ou vendre"), so 5 % false
  alarms. With the fake provider (CI, tests) there is no judge and only the phrases run, and they catch 15 %.
  With a real model that gives no verdict, the claim is removed: safe, but an Ollama crash empties the answer.
- The verifier checks claims, not omissions: an answer that leaves out the most exposed company is not
  caught.

### Likely questions

- *What if the model invents a number?* Test `test_an_invented_number_is_removed_and_the_prose_rebuilt`:
  0.85 appears in no tool result, the claim moves to `removed_claims` with "number not found in a tool
  result about its evidence: 0.85", and the prose keeps only the verified claim.
- *What if it calls a beta a fact?* It is lowered to `estimate`, with the reason "stated as fact, its source
  is a estimate: lowered" (test `test_a_beta_stated_as_a_fact_is_lowered_to_an_estimate`).
- *Why is "because" forbidden?* A beta measures how two prices moved together. Saying one *causes* the other
  is a claim the data cannot support (oil and airlines can both react to the economy).
- *Does the verifier slow the agent down?* The rules: one existence query per label (a few index seeks) and
  a walk through the trace in Python, milliseconds. The advice judge: one short model call per kept claim,
  about 2 s each on a CPU (at most 4 claims), faster on a GPU.
- *How do you know the advice judge works?* `python -m ml.advice_eval`: 40 labelled sentences, English and
  French, half advice and half description, with the hard cases (advice without "buy", descriptions with
  "sells"). None of them is an example inside the judge's prompt. Phrases: 15 % caught. 3B: 85 %. 7B: 100 %,
  5 % false alarms.
- *And if Ollama crashes while judging?* The judge gives no verdict, and a claim with no verdict is removed:
  only a claim the judge called SAFE is shown (test `test_without_a_judge_verdict_the_claims_are_removed`).
  The answer becomes "nothing verified" rather than risk showing advice.
- *Where does the policy come from?* The legal definition of investment advice (MiFID II, as the AMF applies
  it): a recommendation to buy, sell or hold a specific instrument, or presenting one as suitable for the
  reader. Describing an exposure is not advice; telling the reader what to do with it is.
