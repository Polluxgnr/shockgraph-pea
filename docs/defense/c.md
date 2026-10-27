# Defense notes (role c)

Updated at the end of every session. See the end of docs/roles/c.md for the questions to answer.

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
