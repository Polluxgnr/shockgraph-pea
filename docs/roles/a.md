# Role A: data, schema, guardrails, lead (Pollux, `Polluxgnr`)

Read `AGENTS.md` first. You own real-data ingestion, the schema, the Cypher guardrail, CI, the modeling
documentation and the final README. Your PRs are reviewed by **c** (`exilonn`); you review **b** (`tiltoee`).
Branches `feat/a<n>-…`, tests `tests/test_a_*.py`.

As lead you also: keep `main` green (look at `gh run list --branch main` each session), keep
`scripts/hooks/ownership.py` up to date when a new file is needed, and unblock reviews that wait more than
12 hours (ping in the WhatsApp group; admin-merge only as a last resort, said in the PR).

## Step 0: setup (Sun 25, no PR)

1. `git switch main && git pull`; `make env install`; `make role ROLE=a`.
2. `git config user.name` / `user.email` are your GitHub identity; `gh auth status` is logged in.
3. `make test-db`, then `make lint && make test` are green.
4. Try every source once by hand and write what you find in `docs/decisions/a.md` (URL, format, limits):
   SSGA holdings xlsx (verified working: `https://www.ssga.com/library-content/products/fund-data/etfs/emea/holdings-daily-emea-en-spyy-gy.xlsx`,
   MSCI ACWI with ISIN, weight, sector, country), OpenFIGI `POST /v3/mapping`, GLEIF `lei-records?filter[isin]=`,
   Wikidata SPARQL by ISIN (`wdt:P946`).

## Checklist (one issue = one branch = one PR, in order)

### A1 · `feat/a1-universe` (#1, due Mon 26)
`scripts/ingest/universe.py`, `data/seed/funds.csv`, `data/seed/indexes.csv`.
- Index proxies from SSGA daily holdings (ACWI, S&P 500, Europe, Euro area…; list the files in
  `data/seed/indexes.csv` with their URL). Keep the downloaded files in `data/raw/` (git-ignored).
- `Company` (ISIN, name, weight per index, sector, country) + `CONSTITUENT {weight, as_of}`, `Sector`,
  `Industry` (SSGA "Industry Classification"), `Country {eu_eea}`; `pea_eligible` = headquartered in the
  EU/EEA (approximation: document it and its limits, e.g. dual listings).
- OpenFIGI: ISIN → ticker + exchange code → Yahoo symbol (`FP`→`.PA`, `GY`→`.DE`, `NA`→`.AS`, `IM`→`.MI`,
  `SM`→`.MC`, `BB`→`.BR`, `US`→none…). Batches of 10 without a key (25 req/min), respect the limits, cache the
  answers in `data/cache/openfigi.json`. GLEIF LEI when found.
- `data/seed/funds.csv`: about 20 real, hand-checked PEA ETFs (ISIN, name, ticker, issuer, TER,
  replication, PEA, tracked index proxy, source URL of the KID/factsheet) plus 3–4 popular non-PEA ones.
  `TRACKS` to the proxy; `HOLDS` only when the issuer publishes holdings (otherwise leave it out and say so).
- Load with `UNWIND $rows AS row MERGE …` by batches of 1 000; idempotent; print counts per label.
- Tests `tests/test_a_universe.py`: unit tests of the pure functions (Yahoo suffix mapping, EU/EEA rule,
  xlsx row parsing on a tiny in-memory sample) and one integration test loading 3 rows with ISINs starting
  `ZZ` into the test database, then deleting them.

### A2 · `feat/a2-declared-exposures` (#2, due Tue 27)
`scripts/ingest/declared.py`, `data/seed/factors.csv` (exists), `data/seed/industry_factors.csv`.
- `Factor` nodes with their sub-label (`:Commodity`, `:Currency`, `:Rate`).
- `industry_factors.csv`: hand-written map industry → PRODUCES / CONSUMES → factor, each line with a short
  justification (airlines consume oil, miners produce copper, banks… rates are *not* declared: they come
  from betas). This is a modeling choice to defend: fact at industry level vs company level.
- Wikidata by ISIN: industry (P452) and products (P1056) mapped to factors with a keyword table;
  company-level `PRODUCES` / `CONSUMES {source: 'wikidata'}`. Unmatched products are counted, not guessed.
- Tests: mapping functions in unit; one integration test on `ZZ` companies.

### A3 · `feat/a3-news-hypotheses` (#3, due Thu 29)
`scripts/ingest/news.py`, `app/services/hypotheses.py`, tests.
- GDELT DOC 2.0 API (`https://api.gdeltproject.org/api/v2/doc/doc?query=…&mode=artlist&format=json`) for the
  ~100 largest companies, last 90 days; skip URLs already in the graph (`Article.url`).
- Extraction with the **small** local model (`get_chat_model(settings, "small")` from role C; until it exists,
  code against its signature and test with a fake model): structured output
  `{subject, object, kind, sentence, confidence}`; names resolved with `company_name_ft`; an unresolved name
  is counted, never invented; a claim whose sentence does not contain both names is dropped.
- `Claim {status: 'hypothesis'}`; becomes `confirmed` when at least 2 articles from different domains support
  the same (subject, object, kind). Never `fact`.
- `HypothesisService.list_claims` (expected answers in `tests/fixtures/README.md`).

### A4 · `feat/a4-cypher-guardrails` (#4, due Tue 27: role C needs it on Wed)
`app/agent/guardrails.py`, `CypherValidator.validate`. Refuse, with a reason each:
writes (`CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`, `LOAD CSV`, `FOREACH`, `CALL {…} IN TRANSACTIONS`),
procedures other than an allow-list (`db.index.fulltext.queryNodes`, read-only `gds.*.stream`), `apoc.*`,
`dbms.*`, labels and relationship types outside `ALLOWED_LABELS` / `ALLOWED_RELATIONSHIPS`, unbounded or
> 6 variable-length paths, missing `LIMIT` or `LIMIT` > 100, several statements (`;`).
String literals and comments are stripped before checking, so `RETURN "CREATE"` passes and
`/* x */ CREATE` does not hide. Tests `tests/test_a_guardrails.py`: at least 20 refused queries (each with its
expected reason) and 10 accepted; `EXPLAIN` of an accepted query succeeds on the test database.
Also `docs/guardrails.md`: each rule, the attack it blocks, two `curl` examples for the video
(`POST /api/agent/validate-cypher`: one accepted, one rejected).

### A5 · `docs/a5-modeling` (#5, due Fri 30)
- `docs/modeling.md`: why `Factor` with sub-labels rather than three labels; why a synthetic ETF needs
  `HOLDS` **and** `TRACKS`; why prices stay out of the graph; why `Claim` is a node (provenance cannot hang on a
  relationship); why `CORRELATED_WITH` is stored once and top-k; why the portfolio is never stored (personal
  data); declared exposure at industry vs company level.
- **Anti-genericity appendix**: start a sub-agent that receives ONLY this sentence: "Neo4j graph of PEA
  stocks, sectors, commodities and macro factors to simulate the impact of a commodity shock on a student's
  portfolio" and ask it for a graph schema. Paste its answer as is, then compare point by point with
  `docs/schema.md` and explain each difference.
- `docs/schema.drawio` (draw.io XML) + an exported PNG in `docs/img/a_schema.png`.
- `docs/data_sources.md`: every source with link, licence/terms, scope kept, and the real ingestion counts.

### A6 · `docs/a6-readme` (#6, Sat 31: draft from 14:00, merge by 20:00, then tag)
`README.md` assembled from what is merged: pitch, quick start, Makefile commands, `curl` examples, screenshots
(`docs/img/`), schema, architecture and perf (B's docs), GDS and ML (C's), agent and guardrails, data
sources, anti-genericity appendix, badges (lint, tests, publish), image link
(`ghcr.io/polluxgnr/shockgraph-api`), contributions table (`git shortlog -sn main` + merged PRs per member),
disclaimer (educational, not investment advice, correlation ≠ causation). Summarise and link to `docs/`;
never write another role's content for them. Also tick `docs/demo_checklist.md`.
Then: `git switch main && git pull && git tag v1.0 && git push origin v1.0`.

## `docs/defense/a.md` must answer

Why each node vs property choice; what `MERGE` costs without a uniqueness constraint; which index serves which
query; how ISIN, FIGI, LEI and Wikidata ids are reconciled and what fails; how a news hypothesis becomes
"confirmed" and why never a fact; every guardrail rule and the attack it blocks; real ingestion numbers.
