# Defense notes (role a)

Updated at the end of every session. See the end of docs/roles/a.md for the questions to answer.

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
