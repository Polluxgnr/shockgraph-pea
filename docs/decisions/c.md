# Decisions (role c)

One line per decision not settled by AGENTS.md: date, decision, why.

- 2026-10-27 · C2 · Cypher projection of the CORRELATED_WITH pairs only, not of every Company: an isolated stock would get its own community, a base PageRank and a zero FastRP embedding, which mean nothing.
- 2026-10-27 · C2 · Results of the previous refresh are removed before writing, so a stock that lost its correlations does not keep an old community or embedding.
- 2026-10-27 · C2 · Louvain and FastRP with `concurrency: 1` (and FastRP `randomSeed: 42`): the same graph gives the same communities and embeddings, which the tests and the video need.
- 2026-10-27 · C2 · PageRank `maxIterations: 100` instead of 20: on the mini graph's chains and stars it did not converge in 20 (`didConverge = false`).
- 2026-10-27 · C2 · FastRP dimension 64: enough for ~1,500 projected stocks, small enough to store on every node (64 floats) for C3's features.
- 2026-10-27 · C2 · `similar` computes cosine on the written embeddings at request time, not a stored KNN graph: no extra relationships to keep fresh, one pass over ~1,500 vectors per call. Negative cosines are not returned; scores rounded to 6 decimals, ties broken by ISIN.
- 2026-10-27 · C2 · `clusters` shows at most 20 members per community (by PageRank) with the true `size`, and the 3 factors to which the most members have an estimate with r2 >= `min_r2_default`.
