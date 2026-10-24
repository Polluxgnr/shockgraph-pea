# Mini graph: expected answers

`mini_graph.cypher` is a small **fictional** graph (fake ISINs, made-up betas) loaded on a wiped test
database at the start of every pytest session (`tests/conftest.py`). Write your tests against the
answers below. If you need more data, ask role A to append it: never change an existing value.

## Cast

| Ref | Name | Industry → Sector | Country | Market cap (€bn) | PEA |
| --- | --- | --- | --- | --- | --- |
| `Company:FRTEST000011` | Petrolia SA (`PTL.PA`) | OIL_GAS → ENERGY | FR | 120 | yes |
| `Company:FRTEST000029` | AeroLys (`AEL.PA`) | AIRLINES → INDUSTRIALS | FR | 4 | yes |
| `Company:FRTEST000037` | Maison Varenne (`VAR.PA`) | LUXURY → CONS_DISC | FR | 300 | yes |
| `Company:NLTEST000045` | Siliqa NV (`SLQ.AS`) | SEMIS → TECH | NL | 250 | yes |
| `Company:FRTEST000052` | Banque Orsay (`ORS.PA`) | BANKS → FINANCIALS | FR | 45 | yes |
| `Company:DETEST000060` | Autoroute Motoren AG (`AUT.DE`) | AUTO → CONS_DISC | DE | 60 | yes |
| `Company:DETEST000078` | Cuprum Bergbau AG (`CUP.DE`) | MINING → MATERIALS | DE | 15 | yes |
| `Company:FRTEST000086` | Helios Chimie (`HEL.PA`) | CHEMICALS → MATERIALS | FR | 8 | yes |
| `Company:USTEST000094` | Vela Software Inc (`VELA`) | SOFTWARE → TECH | US | 900 | **no** |

Funds:

| Ref | Name | Replication | TER | PEA | TRACKS | HOLDS |
| --- | --- | --- | --- | --- | --- | --- |
| `Fund:FRFUND000011` | Fixture France 40 UCITS ETF | physical | 0.25 | yes | IDX_FR40 | same as the index |
| `Fund:FRFUND000029` | Fixture World PEA UCITS ETF | **synthetic** | 0.38 | yes | IDX_WORLD | Orsay 0.5, Helios 0.5 (substitute basket) |
| `Fund:IEFUND000037` | Fixture World Physical UCITS ETF | physical | 0.20 | no | IDX_WORLD | same as the index |

Indexes (`CONSTITUENT.weight`):
- `IDX_FR40`: Petrolia 0.30, Varenne 0.25, Orsay 0.20, Helios 0.15, AeroLys 0.10
- `IDX_WORLD`: Vela 0.40, Siliqa 0.20, Varenne 0.15, Petrolia 0.15, Cuprum 0.10

Factors: `BRENT`, `COPPER`, `GOLD` (`:Commodity`), `EURUSD` (`:Currency`), `EUR10Y` (`:Rate`).
`GOLD` has **no** relationship at all.

Declared (fact): industry OIL_GAS PRODUCES BRENT; AIRLINES and CHEMICALS CONSUME BRENT; MINING PRODUCES
COPPER; AUTO CONSUMES COPPER; company Siliqa CONSUMES COPPER (source `wikidata`).

`SENSITIVE_TO` (estimate, n_obs 156, window 2023-10-20 → 2026-10-16):

| Company | Factor | beta | r2 |
| --- | --- | --- | --- |
| Petrolia | BRENT | 0.62 | 0.48 |
| AeroLys | BRENT | −0.41 | 0.22 |
| Helios | BRENT | −0.15 | 0.05 |
| Cuprum | COPPER | 0.90 | 0.55 |
| Autoroute | COPPER | −0.20 | 0.08 |
| Siliqa | COPPER | 0.10 | 0.02 |
| Varenne | EURUSD | −0.80 | 0.30 |
| Siliqa | EURUSD | −0.35 | 0.12 |
| Orsay | EUR10Y | 0.55 | 0.25 |

`CORRELATED_WITH` (stored with `a.isin < b.isin`): Cuprum–Petrolia 0.55, Autoroute–Cuprum 0.40,
AeroLys–Helios 0.45, Varenne–Siliqa 0.50, Siliqa–Vela 0.60, Petrolia–Orsay 0.35.

`PREDICTED_SENSITIVITY`: Autoroute → EURUSD, prob 0.71, direction `down`, model `fixture-v0`.

Claims (all `SUPPLIES`):

| Claim | Subject → Object | Confidence | Status | Article |
| --- | --- | --- | --- | --- |
| `c1` | Cuprum → Autoroute | 0.80 | hypothesis | cuprum-autoroute-contract |
| `c2` | Petrolia → Helios | 0.60 | confirmed | petrolia-helios-feedstock |
| `c3` | Siliqa → Autoroute | 0.30 | hypothesis | cuprum-autoroute-contract |
| `c4` | Vela → Varenne | 0.20 | **rejected** | vela-varenne-rumour |

## Counts

Nodes (45): Company 9, Fund 3, Index 2, Sector 6, Industry 9, Country 4, Factor 5, Article 3, Claim 4.

Relationships: TRACKS 3, HOLDS 12, CONSTITUENT 10, IN_INDUSTRY 9, PART_OF 9, HEADQUARTERED_IN 9,
PRODUCES 2, CONSUMES 4, SENSITIVE_TO 9, CORRELATED_WITH 6, PREDICTED_SENSITIVITY 1, SUBJECT 4, OBJECT 4,
EXTRACTED_FROM 4, MENTIONS 7.

## Expected answers

**Search (B1)**
- `q="petrol"` → Petrolia SA only. `q="fixture world"` on funds → two funds (ambiguous name, used by the agent tests).
- `pea_only=true`, no query → 8 companies (not Vela), first one Maison Varenne (largest cap), with `limit=3`:
  Varenne, Siliqa, Petrolia, then the cursor gives Autoroute, Orsay, Cuprum.

**Exposures (B1)**
- Petrolia: `SENSITIVE_TO BRENT` (estimate, 0.62, r2 0.48) and `PRODUCES BRENT` (fact, through its industry).
- `exposed(BRENT, min_r2=0.1)` → Petrolia 0.62 then AeroLys −0.41 (ordered by |beta|). With `min_r2=0` Helios −0.15 is added.
- `exposed(UNKNOWN)` → None (404).

**X-ray (B2)**: positions `[FRFUND000029: 1000 €, FRTEST000011: 500 €]`, look-through on
- total 1500 €. Companies: Petrolia 650 € (0.4333), Vela 400 (0.2667), Siliqa 200 (0.1333), Varenne 150 (0.10), Cuprum 100 (0.0667).
- Sectors: ENERGY 650, TECH 600, CONS_DISC 150, MATERIALS 100. Countries: FR 800, US 400, NL 200, DE 100.
- Factors (estimates with r2 ≥ 0.1 only): BRENT weighted_beta 0.2687 (covered 0.4333); EURUSD −0.1267 (covered 0.2333);
  COPPER 0.06 (covered 0.0667).
- A warning names `FRFUND000029` as synthetic (it physically holds Orsay and Helios, which the look-through ignores).
- Unknown ISIN `XX0000000000` in the positions → listed in `unknown_isins`, no error.

**Shocks (B3)**
- `BRENT +20`, depth 1, min_r2 0.1 → direct: Petrolia +12.4 % (estimate), AeroLys −8.2 % (estimate), Helios with
  `impact_pct = None` (fact: its industry CONSUMES BRENT; its own beta has r2 0.05 < 0.1). No indirect line.
- `EURUSD -5`, depth 2 → direct: Varenne +4.0 %, Siliqa +1.75 %. Indirect: Autoroute (hypothesis, through `Claim:c3`).
  Vela must **not** appear: `Claim:c4` is rejected.
- `BRENT +20` with the x-ray positions above → portfolio impact +80.60 € (Petrolia 650 € × 12.4 %), +5.37 %.
- Unknown factor → KeyError (404).

**Paths (B3)**: `Factor:BRENT` → `Company:FRTEST000029` = 1 hop (SENSITIVE_TO). `Factor:GOLD` → anything: None (404).

**Hypotheses (A3)**: for `DETEST000060` → c1 (0.80) then c3 (0.30). `status=rejected` → c4 only.

**GDS (C2)**: three expected communities on CORRELATED_WITH: {Petrolia, Cuprum, Autoroute, Orsay},
{AeroLys, Helios}, {Varenne, Siliqa, Vela}. Test membership pairs (same / different community), not ids.
