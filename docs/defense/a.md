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

## Numbers (filled after the real run)

See the PR of A1 and `docs/data_sources.md` (A5).
