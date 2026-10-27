# Data sources

Every source is free and needs no paid account, and every key is optional: an OpenFIGI key raises its rate
limit, a FRED key replaces the public CSV export. Each step of `python -m scripts.seed_data` reads its sources, caches the raw answers in
`data/raw/` or `data/cache/` (git-ignored), and writes to Neo4j idempotently.

| Source | What we take | Access | Terms | Step |
| --- | --- | --- | --- | --- |
| [SSGA SPDR ETF holdings](https://www.ssga.com/) (daily xlsx of SPYY, SPY5, ERO, SPYM) | Index constituents with weights, GICS sector and industry, listing country | Public download, no key | Published for investor information; we store derived weights, cite the source and date | A1 universe |
| [OpenFIGI](https://www.openfigi.com/api) | ISIN → every exchange listing (ticker, exchange code, FIGI) | `POST /v3/mapping`, optional free key | FIGI is an open standard; free API | A1 universe |
| [Nasdaq](https://www.nasdaq.com/market-activity/quotes/nasdaq-ndx-index) | Nasdaq-100 members and market caps (anchor of the market-cap estimate) | Public JSON endpoint of nasdaq.com | Website terms of use; low volume (one call per run) | A1 universe |
| [Wikipedia: CAC 40](https://en.wikipedia.org/wiki/CAC_40) | CAC 40 members (Yahoo tickers) | MediaWiki API (`action=parse`) | CC BY-SA 4.0 | A1 universe |
| [ECB Data Portal](https://data.ecb.europa.eu/) | EUR/USD reference rate | `data-api.ecb.europa.eu`, no key | Free reuse with attribution | A1 universe |
| Issuer pages (Amundi, iShares, BNP Paribas AM, Vanguard, Invesco) | 14 ETFs: ISIN, TER, replication, PEA eligibility, tracked index | Hand-checked, `data/seed/funds.csv` with one source URL per line; every ISIN verified with OpenFIGI | Public product documents (KID, factsheet) | A1 universe |
| [Wikidata](https://www.wikidata.org/) | Company entity and products (P1056), looked up by ISIN (P946) | SPARQL endpoint, no key | CC0 | A2 declared |
| Hand-written industry map, `data/seed/industry_factors.csv` | Which GICS industries produce or consume which commodity, one justification per line | Our own work | Ours | A2 declared |
| [Google News RSS](https://news.google.com/) | Headlines, publisher, date and link about the 60 largest PEA companies (never article text) | RSS search, no key | Headlines and links only, for analysis | A3 news |
| [GDELT DOC 2.0](https://www.gdeltproject.org/) | Same search as a second source (optional) | JSON API, one request per 5 s | Open data, citation requested | A3 news |
| [Yahoo Finance](https://finance.yahoo.com/) via `yfinance` | Daily adjusted closes of stocks, commodity futures, FX and two market proxies (STOXX Europe 600 `EXSA.DE`, MSCI World `URTH`), resampled to Friday closes (weekly moves) | No key | Personal / research use | C1 betas (role C) |
| [FRED](https://fred.stlouisfed.org/) and ECB yield curves | Interest-rate series, used as changes in percentage points | FRED key optional (without it, the public CSV export), no key for the ECB | Public domain (FRED series used) / ECB reuse terms | C1 betas (role C) |

## Scope kept

- **Universe**: every constituent of MSCI ACWI, S&P 500, MSCI Europe and MSCI Emerging Markets, as held by SSGA's
  replicating ETFs on the holdings date. That covers the large and mid caps a student can reach, plus the companies
  inside the ETFs they buy.
- **PEA eligibility**: EU/EEA incorporation (ISIN prefix) and EU/EEA home listing. An approximation of the legal
  rule (registered office in the EU/EEA, subject to corporate tax), documented with its limits in
  `docs/decisions/a.md`.
- **Indexes**: 4 from holdings (ACWI, S&P 500, Europe, EM) and 3 derived: World (ACWI minus EM), Nasdaq-100 and
  CAC 40 (member lists weighted by market cap). The derived weights are cap-weighted; the official indexes cap
  their largest members.
- **News**: the 60 largest PEA companies, last 90 days, supply-chain keywords.

## Numbers of the first real runs (holdings of 22 Oct 2026, run on 25 Oct)

| Step | Duration | Result |
| --- | --- | --- |
| A1 universe | 46–65 s (first OpenFIGI pass ~55 min without key) | 2,466 companies (305 PEA-eligible), 66 countries, 11 sectors, 75 industries, 7 indexes, 14 funds, 5,658 `CONSTITUENT`; Yahoo symbol found for 2,371 companies (96 %); CAC 40 resolved 40/40, Nasdaq-100 98/101 |
| A2 declared | 58–72 s | 12 factors; 28 industry links (22 `CONSUMES`, 6 `PRODUCES`); 1,471 companies found on Wikidata; 58 company `PRODUCES`; 1,386 products left unmatched (counted, not guessed) |
| A3 news | 100 s | 2,727 headlines; 281 filtered as corporate finance; 154 candidate pairs; 145 unresolved; 4 claims, all correct on reading; 1 confirmed |
| C1 betas | (role C) | (role C) |

## Reproduce

```bash
make docker-run                                # Neo4j + GDS (and the rest of the stack)
python -m scripts.seed_data                    # all steps, in order
python -m scripts.seed_data --only universe    # or one step
```
