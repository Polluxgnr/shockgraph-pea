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

1. Removes the results of the previous run (`community_id`, `pagerank`, `embedding`).
2. Builds an **in-memory projection** `market`: every pair of stocks linked by `CORRELATED_WITH`, undirected,
   weighted by `rho`.
3. Runs **Louvain** → `community_id`, **PageRank** → `pagerank`, **FastRP** → `embedding` (64 numbers), and writes
   them on the Company nodes.
4. Drops the projection (also when an algorithm fails: `finally`).

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
  the old results are removed first, then recomputed.
