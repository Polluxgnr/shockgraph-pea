"""Models of C3 and the metrics of docs/ml_report.md.

Three ways to rank (company, factor) pairs by how likely the company is exposed:

- baseline: the share of companies of the same industry exposed to that factor in the training window (the
  "industry majority" rule, kept as a share so that it can rank pairs);
- logistic regression on the features (missing peers filled with 0 plus a "was missing" flag, then scaled);
- gradient boosting (scikit-learn's histogram version, which handles missing values itself).

Exposed pairs are a minority, so accuracy would reward a model that never predicts exposure. The metrics are
AUC-PR (average precision: how well the exposed pairs are ranked first) and precision@k (of the k pairs ranked
highest, the share really exposed).
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RANDOM_SEED = 42
VALIDATION_SHARE = 0.25
SPLIT_TRIES = 20
REPORT_KS = (50, 100, 500)
TOP_IMPORTANCES = 8


def candidates() -> dict:
    """The two models compared with the baseline, untrained."""
    return {
        "logistic": make_pipeline(
            SimpleImputer(strategy="constant", fill_value=0.0, add_indicator=True),
            StandardScaler(),
            LogisticRegression(max_iter=2000),
        ),
        "gradient_boosting": HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=RANDOM_SEED
        ),
    }


def industry_baseline(train: pd.DataFrame, pairs: pd.DataFrame, industries: dict) -> np.ndarray:
    """Share of the training window's companies of the same industry exposed to the factor.

    A company of an industry the training window never saw gets the factor-wide share.
    """
    known = train.assign(industry=train["isin"].map(industries))
    by_industry = known.groupby(["industry", "factor"])["label"].mean()
    by_factor = train.groupby("factor")["label"].mean()
    keys = pd.MultiIndex.from_arrays([pairs["isin"].map(industries), pairs["factor"]])
    scores = pd.Series(by_industry.reindex(keys).to_numpy(), index=pairs.index)
    return scores.fillna(pairs["factor"].map(by_factor)).fillna(0.0).to_numpy()


def precision_at_k(labels: np.ndarray, scores: np.ndarray, k: int) -> float:
    """Share of truly exposed pairs among the k highest scores (all pairs when there are fewer than k)."""
    order = np.argsort(-np.asarray(scores), kind="stable")[: min(k, len(scores))]
    return float(np.mean(np.asarray(labels)[order])) if len(order) else 0.0


def evaluate(labels: np.ndarray, scores: np.ndarray, cutoffs: tuple[int, ...] = REPORT_KS) -> dict:
    """AUC-PR and precision@k (for each k in `cutoffs`) of one way of ranking the pairs."""
    return {
        "auc_pr": round(float(average_precision_score(labels, scores)), 4),
        "precision_at": {k: round(precision_at_k(labels, scores, k), 4) for k in cutoffs},
    }


def validation_split(train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(fit, check): about a quarter of the companies held out, both parts holding exposed and unexposed pairs.

    The global check of the labels does not guarantee it: a held-out quarter can, by chance, hold no exposed pair
    (AUC-PR undefined) or take them all (nothing to fit). Up to SPLIT_TRIES splits are tried in a fixed order
    (same seed, so the same run gives the same split); none valid is an explicit error.
    """
    splitter = GroupShuffleSplit(n_splits=SPLIT_TRIES, test_size=VALIDATION_SHARE, random_state=RANDOM_SEED)
    for fit_rows, check_rows in splitter.split(train, groups=train["isin"]):
        fit, check = train.iloc[fit_rows], train.iloc[check_rows]
        if fit["label"].nunique() == 2 and check["label"].nunique() == 2:
            return fit, check
    raise ValueError(
        f"no split of {train['isin'].nunique()} companies puts exposed and unexposed pairs on both sides "
        f"in {SPLIT_TRIES} tries: too few exposed companies to choose a model"
    )


def choose(train: pd.DataFrame, columns: list[str]) -> str:
    """Name of the candidate with the best AUC-PR on companies held out of the *training* window.

    The test window is never used to choose, so its scores stay an honest estimate. Companies, not pairs,
    are held out: the 12 pairs of one company look alike and would make validation too easy.
    """
    fit, check = validation_split(train)
    scores = {}
    for name, model in candidates().items():
        model.fit(fit[columns], fit["label"])
        scores[name] = average_precision_score(check["label"], model.predict_proba(check[columns])[:, 1])
    return max(scores, key=lambda name: (scores[name], name))


def importances(model, table: pd.DataFrame, columns: list[str]) -> list[dict]:
    """Features whose shuffling hurts AUC-PR the most: what the model actually relies on."""
    result = permutation_importance(
        model,
        table[columns],
        table["label"],
        scoring="average_precision",
        n_repeats=5,
        random_state=RANDOM_SEED,
    )
    ranked = sorted(zip(columns, result.importances_mean), key=lambda item: -item[1])
    return [{"feature": name, "auc_pr_drop": round(float(drop), 4)} for name, drop in ranked[:TOP_IMPORTANCES]]
