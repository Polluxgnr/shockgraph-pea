# Defense notes (role b)

Updated at the end of every session. See the end of docs/roles/b.md for the questions to answer.

## B1 — Company and factor search

- Full-text queries use the frozen Neo4j indexes and escape Lucene operators before adding a trailing
  wildcard. User text is always passed in `$query`, never interpolated into Cypher.
- Company lists are ordered by market cap descending then ISIN. The cursor is URL-safe base64 JSON
  containing those two values. The next query uses a range predicate, so its work does not grow with
  the page number as `SKIP` does.
- Exposure facts (`PRODUCES`, `CONSUMES`) remain distinct from statistical estimates
  (`SENSITIVE_TO`). The API never converts a direction-only fact into a numeric impact.
- Query cost is bounded by `limit + 1` for search and by the route limit for factor exposure. The extra
  search row only determines whether a next cursor exists.
- Validation run on 2026-10-28: 147 tests passed against Neo4j 5.26 + GDS; coverage 78.65%; pylint
  10.00/10. Mini-graph checks include the second cursor page and Petrolia's two BRENT truth levels.
