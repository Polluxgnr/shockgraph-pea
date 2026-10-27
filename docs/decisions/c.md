# Decisions (role c)

One line per decision not settled by AGENTS.md: date, decision, why.

- 2026-10-27 · C4 · Advice requests are caught by a rule (English and French patterns) before any model call: a prompt can talk a model into a recommendation, not a regex. The small model only decides in or out of scope, and the question goes on when it fails (scope is a convenience, not a safety rule).
- 2026-10-27 · C4 · An ambiguous name stops the run with a question. Only an exact match (legal suffix aside, or the ticker) skips it: with a prefix rule, "Total" became Total Gabon instead of TotalEnergies.
- 2026-10-27 · C4 · `cypher_fallback` fails closed: if the validator raises (not merged yet) or refuses, the query never reaches Neo4j.
- 2026-10-27 · C4 · Tool errors (a service not written yet, an unknown factor) go to the trace and back to the model as `{"error": ...}`; the run continues.
- 2026-10-27 · C4 · Budget of 6 tool calls counted in the trace; calls over the budget are recorded as skipped, and the graph goes straight to the writer without another planning call.
- 2026-10-27 · C4 · Unusable writer output (bad JSON from the model) gives a fixed answer with no claim, never the planner's free text, which nothing has checked.
- 2026-10-27 · C4 · `ToolCall.result` (added, optional) keeps the full tool result: the verifier (C5) needs every number, the 300-character preview is not enough.
- 2026-10-27 · C4 · Ollama models at temperature 0, so the same question gives the same plan. The fake provider is a scripted model that plays back tool calls and structured answers.
