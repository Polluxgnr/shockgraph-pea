# Defense notes (role A: Pollux)

Updated at the end of every session. Each answer is short enough to say in the video.

## A1 · The investable universe

**Where do the companies come from?**
From the daily holdings of four State Street SPDR ETFs that fully replicate MSCI ACWI, S&P 500, MSCI Europe and
MSCI Emerging Markets. A replicating ETF holds the index constituents with the index weights, so its holdings file
is a free, daily, documented proxy for index composition (official index files are paid).

**Why are World, Nasdaq-100 and CAC 40 "derived"?**
SSGA has no MSCI World fund. MSCI World is by definition the developed part of ACWI, so World = ACWI minus the
EM constituents, renormalised. The Nasdaq-100 member list and market caps come from Nasdaq's own API; the
CAC 40 list from Wikipedia. Their weights are proportional to market cap; the real indexes cap the largest
members, so our weights are slightly more concentrated (documented limit).

**How is an ISIN turned into something we can price?**
OpenFIGI maps an ISIN to every listing of the share. We keep the home listing (the exchange of the ISIN country:
FR → Paris, NL → Amsterdam, US → composite) and convert the exchange code to a Yahoo suffix (`TTE` on `FP` →
`TTE.PA`). Unmapped companies keep `ticker = null` and are counted in the report, never guessed.

**How do you get a market cap without a paid feed?**
In a float-weighted index, `weight = float cap / total float cap`, so `cap = weight × total`. The ACWI total is
calibrated on the real market caps returned by the Nasdaq API (median of cap / weight over the overlap: robust to
a few odd values). Every other index is calibrated on its overlap with ACWI. The property is labelled
`market_cap_method = 'float_estimate_from_index_weights'`: fine to rank and weight, not to value a company.

**What makes a company PEA-eligible here?**
The PEA requires a registered office in the EU/EEA. The ISIN prefix is the country of incorporation, so we use
it, and we also require an EU/EEA home listing: that removes US companies incorporated in Ireland (Medtronic),
which brokers refuse in a PEA. Known limits are listed in `docs/decisions/a.md`.

**Why is `HEADQUARTERED_IN` the incorporation country?**
It is the legal criterion that matters for the PEA and the only one in the source. The listing country is kept as
a property. A Chinese company incorporated in the Cayman Islands points to `KY`: that is correct legally.

**Why are funds a separate label from companies?**
A fund is not exposed to anything by itself: its exposure is the index it tracks. Keeping `Fund` separate means a
look-through always goes `Fund -TRACKS-> Index -CONSTITUENT-> Company`, and a query on companies never mixes
funds in by accident.

**Why no `HOLDS` for real funds?**
Synthetic PEA ETFs publish their substitute basket only on JavaScript pages. The economic exposure is the tracked
index anyway, so the look-through is correct without `HOLDS`; the mini graph shows the difference.

**Idempotency and cost.**
Every write is `UNWIND $rows AS row MERGE (c:Company {isin: row.isin})` by batches of 1 000. `MERGE` first
looks the node up: with the uniqueness constraint `company_isin`, that lookup is an index seek (O(log n)); without
it, each `MERGE` would scan every Company node, so loading n companies would cost O(n²). A second run changes
nothing; constituents that left an index are deleted by comparing `as_of` dates.

## A1 numbers (first real run, holdings of 22 Oct 2026)

- 2,466 companies, **305 PEA-eligible**, 66 countries, 11 sectors, 75 industries, 7 indexes, 14 funds.
- 5,658 `CONSTITUENT` links; every index's weights sum to 1.
- Yahoo symbol found for 2,371 companies (96 %); CAC 40 resolved 40/40, Nasdaq-100 98/101.
- 65 s per run with a warm cache; the first OpenFIGI pass takes ~55 min without a key (~1 min with a free key).
- Sanity checks: ASML estimated at €575 bn (Yahoo ≈ €542 bn); CW8 look-through starts with Apple 5.7 %,
  NVIDIA 5.4 %, Microsoft 4.1 %. LVMH shows €100 bn instead of ≈ €190 bn: that is the **float** cap (the Arnault
  family holds about half), which is exactly what index weights measure. Good question to expect.

## A2 · Declared exposures

**What is "declared" vs "estimated"?** Declared = a statement from a source (Wikidata says Norsk Hydro produces
aluminium; our map says airlines consume jet fuel): truth level `fact`, with `source` and `rationale` on the
relationship. Estimated = a beta from prices (role C): `estimate`, with r² and window. Both can disagree, and the
app shows both rather than merging them.

**Why at industry level and not company level?** Company-level data on inputs does not exist for free; industry
economics is stable and defensible line by line (jet fuel is 20–30 % of airline costs). The company inherits the
dependency through `IN_INDUSTRY`. Wikidata then adds company-level production where it exists.

**Why are currencies and rates not declared?** "A bank is exposed to rates" is true but the sign and size differ by
bank; asserting it would be a guess. Only the regression can say it, as an estimate.

**How do you avoid wrong matches?** Q-ids first (each checked against its label; four wrong ones were caught),
whole-word keywords second ("gold" does not match "golden"), explicit exclusions win (copper hydroxide), and
everything unmatched is counted, not guessed: 1,386 products stay unmatched.

**Why relationship types can't be parameters, and how the code stays safe.** Cypher cannot take a relationship
type as `$param`, so there is one fixed query per type, built from a constant tuple (`PRODUCES`, `CONSUMES`),
never from data.

**Numbers.** 12 factors, 28 industry links, 58 company links from Wikidata (1,471 companies found), 72 s.

## A3 · News hypotheses

**Why are news relations hypotheses and never facts?** A headline is a claim by a journalist, extracted by a pattern
or a model that can be wrong. It is stored as a `Claim` node with its sources, its confidence and its extractor, and
the API labels it `hypothesis` (or `confirmed`, which still means "reported by two publishers", not "true").

**Why is a claim a node and not a relationship?** It needs its own provenance: several articles support it
(`EXTRACTED_FROM`, one per article, with per-article confidence and extractor). A relationship cannot point to
other nodes, so the claim is reified.

**How is confidence computed?** Each publisher votes once with its best confidence; votes are combined with a
noisy-OR (1 − Π(1 − c)) and capped at 0.95. Ten syndicated copies of one Reuters story are one vote. Two
publishers at 0.6 give 0.84.

**How do you stop the LLM inventing relations?** It only proposes; Python decides. Names must be copied from the
headline (else dropped), must resolve to companies in the graph through the full-text index and a name check,
and must be two different companies. The rule extractor gives a model-free baseline to compare with.

**What went wrong on real data, and what did you change?** JV and banking headlines looked like contracts; "Shell"
and "New" in Title Case headlines resolved to companies. Each became a rule and a test. Final run: 4 claims out of
2,727 headlines, all correct on reading. Recall is low because most suppliers are outside our universe; we
prefer missing a relation to inventing one.

**Numbers.** 60 companies scanned, 2,727 headlines, 281 filtered, 154 candidates, 145 unresolved, 4 claims,
1 confirmed, 100 s.

## A4 · Cypher guardrail

**Why typed tools first and generated Cypher only as a fallback?** A typed tool runs a query we wrote, tested and
profiled, with parameters: the LLM only chooses the tool and the values. Generated Cypher is unbounded in what it
can express, so it is the exception and goes through the validator.

**Why an allow-list rather than a block-list?** A block-list must think of every attack; an allow-list only of what
we need. Unknown procedures, labels, clauses are refused by default.

**Why strip strings and comments first?** So that `RETURN "DELETE"` is not refused (a company could be named
like a keyword) and `DETACH /* x */ DELETE` is not missed.

**Why bound paths at 6 and LIMIT at 100?** The number of paths grows roughly like (average degree)^hops: on a
correlation graph with ~5 neighbours per stock, 6 hops is already ~15 000 paths per start node; unbounded, it is
the whole connected component. 100 rows is more than an answer needs and keeps the LLM context small.

**Limits.** Regex, not a parser: it can refuse a valid exotic query (safe direction); it does not judge whether a
read query is semantically right: the verifier does that on the answer.

**How are patterns hidden in maps and lists found?** Brackets are read innermost first: a bracket right after `-` is
a relationship (types and length checked), any other is a list (its labels and quantifiers checked, then replaced
by `0`). Map keys are removed only inside a real map, so `beta: r` is never read as the label `r`.

**Why must every UNION branch have a LIMIT?** In Cypher the LIMIT after the last RETURN applies to the last branch
only: `... RETURN c.name AS n UNION ... RETURN f.name AS n LIMIT 5` returned 2,470 rows on the dev database. Same
idea for a LIMIT inside `COUNT { }`: the rule reads the top level only.

**LIMIT bounds the rows, not the work.** `MATCH (a:Company), (b:Company), (c:Company) RETURN count(*) AS n LIMIT 1`
passes every rule and builds n³ rows. A regex cannot see cost, so the database stops it: `read_with_timeout`
sends a server-side transaction timeout (5 s), and the READ routing makes Neo4j refuse any write that slipped
through. Three layers: validator, read transaction, timeout.

**Why refuse every non-ASCII character outside strings?** Neo4j 5.26 reads ten Unicode dashes as `-` and four
angle brackets as `<`: `(a)–[:X*]–(b)` is a real unbounded path that a validator reading ASCII would take for a
list. Listing the characters fixes today's parser; refusing all of them also covers tomorrow's. The LLM writes
ASCII, and company names with accents stay valid inside strings.

**What did the review bring?** Saty's five rounds on #23 found nine bypasses (the ninth: Unicode dashes) (an unbounded quantifier after a
relationship, a LIMIT expression, a path hidden in a map value, a list inside relationship brackets, a label after
a comma, UNION, the work bound...). I then looked for more myself and found five (a LIMIT inside `COUNT {}`, a
comment apostrophe hiding the next line, a procedure called without parentheses, dynamic labels,
`IN CONCURRENT TRANSACTIONS`). Each is a test case.

Numbers: 50 refused and 18 accepted queries in `tests/test_a_guardrails.py`; every accepted query compiles
(`EXPLAIN`) on Neo4j in CI.
