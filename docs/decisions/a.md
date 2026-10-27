# Decisions (role A)

One line per decision not settled by AGENTS.md: date, decision, why.

## Sources explored (25 Oct)

| Source | Endpoint | Format and limits | Verdict |
| --- | --- | --- | --- |
| SSGA holdings | `ssga.com/library-content/products/fund-data/etfs/emea/holdings-daily-emea-en-<key>.xlsx` | xlsx, 5 header lines then ISIN, name, weight %, listing country, GICS sector (11) and industry (75), market value. Daily. No key. | Used for 4 indexes: ACWI `spyy-gy` (2,248 lines), S&P 500 `spy5-gy` (504), MSCI Europe `ero-fp` (384), MSCI EM `spym-gy` (1,174). `swrd` (MSCI World) does not exist: World is derived. |
| iShares holdings | `.ajax?fileType=csv` | Now returns the HTML page (site redesign). | Rejected. |
| OpenFIGI | `POST api.openfigi.com/v3/mapping` | No key: 25 req/min × 10 ISINs. With a free key: 25 req/6 s × 100. Occasional read timeouts. | Used: ISIN → home listing → Yahoo symbol. Cached in `data/cache/openfigi.json`. |
| Nasdaq | `api.nasdaq.com/api/quote/list-type/nasdaq100` | JSON, 101 lines with symbol and market cap. Needs a browser User-Agent. | Used: Nasdaq-100 members and the market-cap anchor. |
| Wikipedia | `en.wikipedia.org/wiki/CAC_40` | HTML table with Yahoo tickers. Needs a User-Agent. | Used: CAC 40 members. |
| ECB Data Portal | `data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A` | JSON. | Used: EUR/USD. |
| GLEIF | `api.gleif.org/api/v1/lei-records` | JSON, filter by name or ISIN. | Not needed for A1 (LEI is optional); kept for A2 if parent companies are needed. |
| yfinance `fast_info` | per ticker | ~3 s per ticker even with 8 threads, some gaps. | Rejected for market caps (2,300 tickers ≈ 2 h). |

## Decisions

- 2026-10-25 · Index constituents come from SSGA SPDR ETFs that fully replicate the index: weights are the fund's
  weights, which follow the index within tracking error. Documented as "proxy" in each `Index.name`.
- 2026-10-25 · `IDX_WORLD` = ACWI minus MSCI EM constituents, renormalised (MSCI World is the developed part of ACWI).
- 2026-10-25 · `IDX_NASDAQ100` weights are proportional to Nasdaq market caps (the real index is modified
  cap-weighted: big names are slightly capped). `IDX_CAC40` weights are proportional to our float-cap estimates
  (the real index caps a member at 15 %).
- 2026-10-25 · `market_cap_eur` is a **float** market cap estimated from index weights: the ACWI total is
  calibrated on the real Nasdaq market caps (median of cap / weight), then each regional index on its overlap
  with ACWI. Stored with `market_cap_method = 'float_estimate_from_index_weights'`. Good for ranking and
  weighting, not for valuation.
- 2026-10-25 · `HEADQUARTERED_IN` points to the ISIN country prefix, i.e. the country of **incorporation**
  (a Cayman-incorporated Chinese company points to KY). It is the legal criterion of the PEA. The listing country
  is kept as `Company.listing_country`.
- 2026-10-25 · PEA eligibility rule: EU/EEA incorporation **and** EU/EEA home listing (or unknown listing).
  Removes US companies incorporated in Ireland (Medtronic, Accenture...) that brokers refuse in a PEA. Known limits:
  dual listings, UK companies are excluded since Brexit (correct), Swiss companies are excluded (correct).
- 2026-10-25 · Funds: 14 ETFs in `data/seed/funds.csv`, every ISIN checked with OpenFIGI. TERs and replication
  come from issuer pages; the rows marked "to check" in the `checked` column must be verified before the video.
- 2026-10-25 · `HOLDS` is not loaded for real funds: issuers publish the substitute baskets of synthetic PEA ETFs
  only through JavaScript pages. The look-through uses `TRACKS`, which is the economic exposure anyway; the
  fixture shows the HOLDS vs TRACKS difference.
- 2026-10-25 · Sector and industry codes are slugs of the GICS names (`INFORMATION_TECHNOLOGY`), so the same name
  always gives the same node.
- 2026-10-25 · Member lists (CAC 40, Nasdaq-100) are resolved through **every** listing of each ISIN, not only the
  home one: Airbus is home-listed in Amsterdam but the CAC 40 names it `AIR.PA`. Result: CAC 40 40/40,
  Nasdaq-100 98/101 (ASML, ARM: the Nasdaq line is an ADR with another ISIN; NBIS is not in ACWI).
- 2026-10-25 · More exchanges mapped to Yahoo (Shanghai `.SS`, Shenzhen `.SZ`, Saudi `.SR`, Qatar, Turkey, Kuwait,
  Chile, Egypt): Yahoo symbols found for 2,371 of 2,466 companies (96 %). Taiwan OTC and UAE lines stay without.
- 2026-10-25 · CAC 40 members come from the MediaWiki API: the plain page answers 403 to scripts.

## First real run (25 Oct, holdings of 22 Oct)

`python -m scripts.seed_data --only universe`: 65 s with a warm OpenFIGI cache (the first OpenFIGI pass takes
~55 min without an API key). 2,466 companies (305 PEA-eligible), 66 countries, 11 sectors, 75 industries,
7 indexes, 14 funds; 5,658 CONSTITUENT, 2,466 IN_INDUSTRY, 2,466 HEADQUARTERED_IN, 75 PART_OF, 14 TRACKS.
Running it twice gives the same counts. EUR/USD 1.1193 (ECB).

## Review of #24 (27 Oct)

- 2026-10-27 · Saty found that for ISINs without a home rule the fallback picked the **US OTC line** (Tencent →
  `TCTZF`, National Bank of Greece → `NBGIF`): 216 companies, some PEA-eligible; OTC prices barely move, which
  would pull betas towards 0. Now: exchanges of the **listing country** (from SSGA) first, then of the ISIN country
  (new rules for GR, CZ, HU, IL, TH, MY, ID, PH, NZ, KY, BM, codes checked against the OpenFIGI answers), then a
  fallback **without US**; a US line is used only when the share trades in the US. Result: 2,367 tickers,
  **0 OTC-looking tickers** (was 6 after the first fix, and ~216 before). Aegon → `AGN.AS`, Tencent → `0700.HK`,
  CEZ → `CEZ.PR`. No ticker is better than an OTC ticker: C1 skips companies without one.
- 2026-10-27 · `Company.as_of` = date of the last holdings file the company was in. A company that leaves every
  index keeps its old date, so the current universe is `WHERE c.as_of = $latest` (betas, search, ML features).
