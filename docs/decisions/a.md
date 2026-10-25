# Decisions (role a)

One line per decision not settled by AGENTS.md: date, decision, why.

## A3 · News hypotheses (25 Oct)

- 2026-10-25 · GDELT DOC 2.0 answered 429 ("one request every 5 seconds") even at 8 s spacing: the IP stays
  throttled for a while. Primary source is Google News RSS (keyless, steady); GDELT is kept as an optional source.
  Only headline, publisher, date and link are stored, never article text.
- 2026-10-25 · Two independent extractors: precise **rules** (no model needed, confidence 0.6) and the small
  **local LLM** with structured output when Ollama answers (capped at 0.9). C's `get_chat_model` still raises
  `NotImplementedError`, so the first real runs used rules only; the LLM path is tested with a fake model.
- 2026-10-25 · Guards, all counted in the report: corporate-finance headlines (JV, stake, bid, merger, advisers)
  are not supply; the LLM may only copy names that are in the headline; both names must resolve to two different
  companies of the graph; company names that are common words (Shell, New, Orange, Target...) are refused.
- 2026-10-25 · Confidence = noisy-OR over **publishers** (best confidence per publisher), capped at 0.95.
  `confirmed` = at least two publishers. Syndicated copies of one story are not independent evidence.
- 2026-10-25 · Real-run false positives that drove these rules, kept as tests: "TotalEnergies signs JV deal with
  Petronas", "Siemens Energy taps Goldman Sachs for stake sale", "Rheinmetall wins 155mm Shell order" (artillery
  shell → Shell plc), "Rheinmetall receives New Order" ("New" → New Oriental Education).
- 2026-10-25 · `HypothesisService` uses `CALL (q) { ... }`: the `CALL { WITH q }` form is deprecated in Neo4j 5.26.

Third real run (60 largest PEA companies, 90 days): 100 s, 2,727 headlines, 281 filtered as corporate finance,
154 candidate pairs, 145 unresolved (supplier outside our universe or ambiguous), 9 pieces of evidence, **4 claims,
all true on reading**, 1 confirmed (SLB → TotalEnergies, 0.95). Precision first, recall low by design.
