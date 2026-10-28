# ML report: predicting exposures from the graph (C3)

Run of 28 October 2026 on the dev database. Every number below comes from `ml/artifacts/metrics.json`
(`python -m ml.train`, or `make ml`).

## The question

C1 writes a `SENSITIVE_TO` beta only where a stock's own prices show a significant link to a factor. The model
asks whether a stock's **position in the graph** is enough to tell that it is exposed: its declared PRODUCES /
CONSUMES links, its industry, its correlation neighbours, its Louvain community and its FastRP embedding.

One row is one pair (company, factor), for example (ENGIE, EUR10Y). The label is 1 when C1's rule would
write a `SENSITIVE_TO` for that pair (|t| >= 2 or partial r2 >= 0.05 in a market-controlled regression on
weekly returns), 0 otherwise.

## Data

| | Training window | Test window |
| --- | --- | --- |
| Weeks | 2020-10-25 → 2023-10-24 | 2023-10-25 → 2026-10-23 |
| Pairs (company, factor) | 24,492 | 25,308 |
| Exposed share | 16.4 % | 14.2 % |

12 factors (Brent, WTI, European gas, copper, aluminium, gold, wheat, EUR/USD, EUR/GBP, EUR/CNY, the euro and
US 10-year rates) and about 2,100 stocks with 6 years of usable weekly prices (Yahoo Finance; the FRED and ECB
series for the rates).

## Temporal split

The model is fitted on the training window and evaluated on the test window, which it never sees. Every
feature and every label is computed inside its own window, from that window's prices only: the regressions,
the residual correlations, and the Louvain communities and FastRP embeddings (GDS runs on a temporary
projection of each window's correlations, with C2's settings).

The choice between the two models uses the training window only: a quarter of its companies are held out
(companies, not rows, because the 12 rows of one company look alike) and the best AUC-PR there wins.

## Features

| Feature | What it measures |
| --- | --- |
| `produces`, `consumes` | a declared PRODUCES / CONSUMES link, on the company or its industry |
| `european` | the stock trades in Europe |
| `industry_rate`, `_beta`, `_peers` | share of the other companies of the industry exposed to the factor, their mean beta, their number |
| `neighbour_rate`, `_beta`, `_peers` | the same for the CORRELATED_WITH neighbours |
| `community_rate`, `_beta`, `_peers` | the same for the other members of the Louvain community |
| `knn_rate`, `_beta`, `_peers` | the same for the 10 nearest companies by FastRP embedding (cosine) |
| `factor_*` | which factor the row is about |

The pair's own regression is only its label. The industry and community averages leave the company out;
neighbours never include it.

## Results

| | Baseline (industry share) | Logistic regression | Gradient boosting |
| --- | --- | --- | --- |
| AUC-PR | 0.270 | 0.572 | **0.573** |
| precision@50 | 0.56 | 0.88 | **1.00** |
| precision@100 | 0.75 | 0.94 | **1.00** |
| precision@500 | 0.61 | 0.95 | **0.97** |

A random ranking would score about 0.14 in AUC-PR (the share of exposed pairs). The industry baseline reaches
0.27; both models reach 0.57, about twice the baseline. The two models are almost tied on AUC-PR; gradient
boosting, chosen on the held-out companies of the training window, is better at the top of the list: its 100
most confident pairs are all exposed in the test window.

## What the model relies on

Drop in test AUC-PR when one feature is shuffled (permutation importance, gradient boosting):

| Feature | AUC-PR lost |
| --- | --- |
| `neighbour_rate` | 0.312 |
| `industry_rate` | 0.029 |
| `knn_rate` | 0.023 |
| `neighbour_peers` | 0.013 |
| `neighbour_beta` | 0.011 |
| `industry_beta` | 0.006 |

Almost everything comes from the correlation neighbours: a stock whose closest co-movers are exposed to a
factor is very likely exposed itself. The industry and the embedding add a little; the declared links and the
community add almost nothing once the neighbours are known.

## Predictions written

The chosen model scored the 26,013 pairs of the graph that have no `SENSITIVE_TO`. The 676 pairs at
probability 0.5 or more (485 companies, 93 pairs on 62 PEA-eligible companies) got a
`PREDICTED_SENSITIVITY {prob, direction, model_version: "gradient_boosting-2026-10-23"}`. The direction is the
sign of the peers' mean beta.

Examples among PEA-eligible companies:

| Company | Factor | Probability | Direction |
| --- | --- | --- | --- |
| ENGIE | US 10-year rate | 0.95 | down |
| ENGIE | euro 10-year rate | 0.95 | down |
| FinecoBank | euro 10-year rate | 0.83 | up |
| Deutsche Bank | US 10-year rate | 0.78 | up |
| Boliden | European gas | 0.77 | down |

Utilities that fall when rates rise, banks that rise with them, a smelter that suffers from expensive gas:
the predictions follow known economic links, although the model was never told about them.

## Limits

- **Same-window features.** Inside a window, the neighbours' exposure and the label are measured on the same
  prices, and two stocks often correlate *because* they follow the same factor. Part of the 0.57 is this
  mechanical link. The split guarantees that nothing from the test period reaches training; it does not make
  the features independent of the label inside a window. A stricter test, features from the older window and
  labels from the recent one, would measure whether the graph *anticipates* an exposure, and is the next step.
- **Noisy labels.** C1's rule lets about 5 % of truly zero betas through by chance (about 28,000 tests per
  window), so part of the "exposed" labels are noise.
- **Coverage.** About 260 of the 2,368 tickers have no usable Yahoo history (C1 has prices for 2,109). Such a stock has no
  neighbours, community or embedding; on its industry alone the model stays below 0.5 (checked on synthetic
  data in `tests/test_c_ml.py`), so it gets no prediction.
- **A prediction is not an estimate.** It is shown with the truth level `prediction`, its probability and the
  model version, and only where C1 has no estimate.

## Reproduce

```bash
make docker-run      # or only the Neo4j service
make seed            # universe, declared links, C1 betas
make ml              # downloads 6 years of prices in rounds, trains, writes the predictions
```

The model and the metrics are saved in `ml/artifacts/` (not versioned).
