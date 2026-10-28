# Role B performance report

Measured on 2026-10-28 against the dedicated Neo4j 5.26 benchmark container at
`bolt://localhost:7689`. The loader refused every other URI. The graph contained exactly 20,000
companies, 100,000 `SENSITIVE_TO` relationships, 200,000 `CORRELATED_WITH` relationships and 50
funds (400 constituents each). Figures below are ten measured calls after one warm-up; they are local
measurements, not cloud measurements.

## Service latency

| Method | p50 ms | p95 ms |
| --- | ---: | ---: |
| `CompanyService.search` | 135.111 | 231.242 |
| `CompanyService.get` | 6.388 | 17.035 |
| `CompanyService.exposures` | 7.607 | 18.886 |
| `CompanyService.resolve` | 20.079 | 45.957 |
| `FactorService.list_factors` | 4.399 | 8.475 |
| `FactorService.exposed` | 25.660 | 37.592 |
| `FactorService.resolve` | 6.354 | 10.365 |
| `PortfolioService.xray` (10 funds / 4,000 expanded lines) | 126.050 | 189.028 |
| `ShockService.simulate` (10,000 direct estimates) | 521.305 | 555.583 |
| `ShockService.path` (`*..6`) | 6.990 | 12.076 |

The unusually large shock result is intentional stress: one synthetic factor is connected to half the
companies. Production routes should add a result-size policy if this worst-case cardinality is expected.

## PROFILE evidence and query choices

| Query | Main operator | Rows | DB hits | Observed time ms |
| --- | --- | ---: | ---: | ---: |
| company by ISIN | `NodeUniqueIndexSeek` | 1 | 3 | 1 |
| companies exposed to one factor, `r2 >= 0.1`, limit 20 | traversal + top/order | 20 | 40,024 | 86 |

The saved PROFILE plan is [b_profile.png](img/b_profile.png). The unique constraint produces
`NodeUniqueIndexSeek`, avoiding a label scan. Full-text name search uses the frozen
`company_name_ft` procedure. `sensitive_r2` exists for relationship-property filtering, while starting
from the factor keeps the traversal anchored.

Company pagination uses the `(market_cap_eur, isin)` range cursor and fetches `limit + 1`; it never uses
`SKIP`. Therefore page 200 does not discard the preceding 3,980 rows. Paths interpolate only a validated
integer bound and never exceed `*..6`; an unbounded graph walk is not used by the application because its
work can grow exponentially with average degree.

Neo4j's Python wrapper deliberately exposes only plain result dictionaries. Exact PROFILE summary data
was therefore collected with `cypher-shell --format verbose` against the same dedicated container, not by
changing the shared database client owned by role A. No unmeasured before/after values are claimed.

## Reproduction

```bash
make bench-db
python -m scripts.bench_data --uri bolt://localhost:7689 --password benchpassword
python -m scripts.benchmark --uri bolt://localhost:7689 --password benchpassword --iterations 30
```

The checked-in table records the completed ten-iteration validation run. A longer run can replace it
before the final presentation if the execution environment remains stable.
