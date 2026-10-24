# Defense notes (role a)

Updated at the end of every session. See the end of docs/roles/a.md for the questions to answer.

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

Numbers: 29 refused and 10 accepted queries in `tests/test_a_guardrails.py`; every accepted query compiles
(`EXPLAIN`) on Neo4j.
