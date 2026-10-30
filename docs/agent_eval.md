# Agent evaluation

## Scope

`python -m scripts.agent_eval` defines 30 fixture-grounded questions: 20 analytical questions and ten
traps covering investment advice, writes, an invented company, ambiguity, unsupported causation and
out-of-scope requests. Each case checks both an expected phrase and the expected refusal flag.

## Reproducible harness result

Run on 2026-10-28:

```text
provider: fake
validation: scripted-harness-only
cases: 30
traps: 10
accuracy: 100% (30/30)
correct refusals: 10/10
removed claims: 0
tool errors: 0
```

This is a validation of the evaluation code, **not an LLM quality result**. The scripted double returns
the fixture expectation, so its accuracy cannot be compared with a model. Its latency, removed-claim and
tool-error values are likewise not representative of a real agent.

## Real-model status

The real mode is available through `python -m scripts.agent_eval --provider real`. It constructs the
frozen `ShockAgent` interface against the test database and records the same metrics. At the time of this
run, role C's `ShockAgent.ask` still raises `NotImplementedError`; the command therefore exits with the
explicit message `real agent is unavailable: C4 ShockAgent.ask is not implemented` and writes no result
file. No real-model accuracy or latency is claimed.

Once C4/C5 is merged, load the mini graph on port 7688, start the local model, run the real command once,
and append its provider, model, accuracy, removed claims, rejected tool/Cypher errors, correct refusals and
mean latency here. The generated JSON is evidence for the run but is intentionally not committed.
