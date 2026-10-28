# Role B performance report

Measured on 2026-10-28 against the dedicated Neo4j 5.26 Community benchmark container at
`bolt://localhost:7689`. The loader refused every other URI. The graph contained exactly 20,000
companies, 100,000 `SENSITIVE_TO` relationships, 200,000 `CORRELATED_WITH` relationships and 50
funds (400 constituents each). Service figures are 50 measured calls after three warm-ups.

Hardware: Intel Core Ultra 9 185H (16 physical cores, 22 logical processors), 31.4 GiB host RAM,
Windows host, Docker Desktop 29.9.2 with 15.3 GiB visible to its Linux VM. These are local measurements,
not cloud measurements; no other workload was intentionally started during the run.

## Service latency

| Method | p50 ms | p95 ms |
| --- | ---: | ---: |
| `CompanyService.search` | 148.723 | 381.679 |
| `CompanyService.get` | 7.988 | 12.314 |
| `CompanyService.exposures` | 8.179 | 13.375 |
| `CompanyService.resolve` | 26.233 | 40.266 |
| `FactorService.list_factors` | 5.623 | 7.270 |
| `FactorService.exposed` | 24.629 | 33.751 |
| `FactorService.resolve` | 7.245 | 10.808 |
| `PortfolioService.xray` (10 funds / 4,000 expanded lines) | 155.945 | 196.972 |
| `ShockService.simulate` (10,000 direct estimates) | 736.871 | 957.107 |
| `ShockService.path` (`*..6`) | 9.152 | 16.711 |

The unusually large shock result is intentional stress: one synthetic factor is connected to half the
companies. Production routes should add a result-size policy if this worst-case cardinality is expected.

## EXPLAIN / PROFILE evidence

Each row was first run with `EXPLAIN` to record the planned operators, then with `PROFILE` to execute it.
The operator shown is the distinguishing scan/seek operator; rows, total DB hits and duration come from
the PROFILE summary. Indexes were recreated in a `finally` block and awaited before the next measurement.

| Comparison | Distinguishing operator | Rows | DB hits | PROFILE ms |
| --- | --- | ---: | ---: | ---: |
| ticker lookup, `company_ticker` dropped | `NodeByLabelScan` | 1 | 40,002 | 58 |
| ticker lookup, `company_ticker` recreated | `NodeIndexSeek` | 1 | 3 | 59 |
| page 200 `SKIP 3980`, market-cap index dropped | `NodeByLabelScan` | 20 | 60,001 | 67 |
| page 200 `SKIP 3980`, market-cap index present | `NodeIndexScan` | 20 | 8,003 | 50 |
| page 200 cursor, market-cap index present | `NodeIndexSeekByRange` + `Union` | 20 | 32,042 | 190 |
| exposed companies, `sensitive_r2` dropped | `NodeUniqueIndexSeek` + `Expand(All)` | 20 | 50,004 | 138 |
| exposed companies, `sensitive_r2` recreated | `NodeUniqueIndexSeek` + `Expand(All)` | 20 | 50,004 | 114 |
| x-ray expansion, ten funds / 4,000 constituents | `NodeUniqueIndexSeek` + `Expand(All)` | 4,000 | 16,080 | 182 |
| shortest path `*..6`, target ten logical steps away | `ShortestPath` | 0 | 2,245 | 48 |
| shortest path unbounded, same nodes | `ShortestPath` | 1 | 4,300 | 23 |

The saved PROFILE plan is [b_profile.png](img/b_profile.png). The unique constraint produces
`NodeUniqueIndexSeek`, avoiding a label scan. The ticker before/after demonstrates the largest index
effect: 40,002 down to 3 DB hits. The exposed query is anchored by the unique factor code and expands its
relationships; Neo4j did not select the `sensitive_r2` relationship index, so recreating it changed elapsed
time but not the 50,004-hit plan. This negative result is retained rather than claiming an unused index.

Company pagination uses the `(market_cap_eur, isin)` range cursor and fetches `limit + 1`; it never uses
`SKIP`. On this dataset the new market-cap index reduced page-200 `SKIP` from 60,001 to 8,003 hits, but the
current cursor predicate compiled to a `Union` and cost 32,042 hits. The cursor still provides stable pages
under insertions and avoids an offset proportional to the page number, but this measurement proves the
current Cypher is not yet a latency win. A composite/range-friendly rewrite is a future optimisation.

For the selected path pair, bounded `*..6` correctly returned no row with 2,245 hits; the unbounded search
found the ten-step path with 4,300 hits. Its shorter reported server time is run-to-run/cache noise, not
evidence that unbounded traversal is safer. The application retains the validated bound because worst-case
work grows rapidly with degree and an unbounded query has no service-level cost ceiling.

## Reproduction

```bash
make bench-db
python -m scripts.bench_data --uri bolt://localhost:7689 --password benchpassword
python -m scripts.benchmark --uri bolt://localhost:7689 --password benchpassword --iterations 50
```

The JSON output contains the full EXPLAIN and PROFILE operator lists plus all latency samples' aggregate
statistics. It is generated evidence and is intentionally not committed.
