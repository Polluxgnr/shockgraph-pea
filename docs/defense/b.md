# Defense notes (role b)

Updated at the end of every session. See the end of docs/roles/b.md for the questions to answer.

## B2 — Portfolio x-ray

- One parameterised Cypher query receives at most 50 positions through `UNWIND`. A stock maps directly
  to itself; a fund follows `TRACKS` then `CONSTITUENT`, including synthetic funds. `HOLDS` is the
  physical substitute basket and is deliberately ignored for economic exposure.
- The query groups expanded amounts by company, sector and country with `WITH`, `sum` and `collect`.
  Factor beta is weighted by total portfolio value and only covers company estimates with `r2 >= 0.1`.
- Complexity is proportional to submitted positions plus tracked constituents, then to the sensitivity
  edges of the resulting companies. Unknown ISINs do not abort the calculation; synthetic funds produce
  an explicit warning.
- Validation run on 2026-10-28: all 140 tests on the branch passed against Neo4j 5.26 + GDS, including
  every documented fixture amount; coverage 77.59%; pylint 10.00/10.
