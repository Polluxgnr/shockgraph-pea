# Decisions (role b)

One line per decision not settled by AGENTS.md: date, decision, why.

- 2026-10-28: missing market caps sort as `-1.0`; this keeps them after known caps and makes the cursor stable.
- 2026-10-28: company `sector` exposes the human-readable sector name while the filter accepts code or name.
- 2026-10-28: portfolio totals include unknown positions; this preserves the submitted denominator while
  `unknown_isins` makes the uncovered amount explicit.
- 2026-10-28: indirect shocks traverse non-rejected supply claims one frontier at a time; this keeps the
  requested depth explicit and never turns a news hypothesis into a numeric causal estimate.
- 2026-10-28: validation errors use one compact 422 envelope with field-level details; this keeps client
  handling stable while preserving Pydantic's precise reason for every rejected value.
