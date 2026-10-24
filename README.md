# ShockGraph PEA

[![lint](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/lint.yml/badge.svg)](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/lint.yml)
[![tests](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/test.yml/badge.svg)](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/test.yml)
[![publish](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/publish.yml/badge.svg)](https://github.com/Polluxgnr/shockgraph-pea/actions/workflows/publish.yml)

**If oil goes up 20 % or the euro drops 5 %, what happens to my PEA, and through which links?**

More and more French students open a PEA and stack a "World" ETF, an S&P 500 ETF and a few stocks,
thinking they are diversified. ShockGraph PEA maps PEA-eligible stocks, their sectors, the commodities,
currencies and rates they depend on, and the ETFs that hold them, as a Neo4j graph. It shows what a
portfolio is really betting on, how a shock spreads through the graph, and explains it with a local
LangGraph agent that never mixes facts, statistical estimates, model predictions and news hypotheses.

> Educational and analytical project. **Not investment advice**: it never recommends buying or selling.
> Statistical sensitivities describe the past; correlation is not causation.

🚧 Work in progress (CentraleSupélec AIDAMS GraphDB project, due 1 Nov 2026). This README is completed
on 31 Oct from the documents in [`docs/`](docs/).

## Quick start

Requirements: Docker Desktop (≥ 8 GB RAM for the local LLM), Python 3.11+, `make`
(Windows: run it from Git Bash after `winget install ezwinports.make`).

```bash
git clone https://github.com/Polluxgnr/shockgraph-pea.git && cd shockgraph-pea
make env install        # .env (edit the passwords) + .venv + git hooks
make docker-run         # nginx :80 -> api -> neo4j + gds, ollama
make llm-pull           # once: local models for the agent
make seed               # real data: SSGA, OpenFIGI, GLEIF, Wikidata, Yahoo, FRED, ECB, GDELT
```

Swagger: http://localhost/docs · Neo4j browser: http://localhost:7474

## Data sources (all free)

| Source | Used for |
| --- | --- |
| SSGA daily ETF holdings | Index constituents and weights, sectors, countries |
| OpenFIGI | ISIN → exchange ticker |
| GLEIF | Legal entity identifiers |
| Wikidata | Industries and products (declared commodity exposure) |
| Yahoo Finance | Stock, commodity and FX prices (sensitivities, correlations) |
| FRED, ECB Data Portal | Interest rates |
| GDELT | News articles (supply-chain hypotheses) |
| Ollama (local) | LLM for the agent and the extraction, free and offline |

## Team

| Role | Member | Scope |
| --- | --- | --- |
| A (lead) | Pollux | Data ingestion, schema, Cypher guardrails, CI, modeling docs |
| B | Eliott | API services, Docker / Nginx / Ollama, performance, agent evaluation |
| C | Saty | Betas and correlations, GDS, ML, LangGraph agent, notebook |

How we work: [`AGENTS.md`](AGENTS.md) (rules), [`docs/roles/`](docs/roles/) (checklists),
[issues](https://github.com/Polluxgnr/shockgraph-pea/issues) (tasks).
