"""Features of the sensitivity model (role C, C3): one row per (company, factor) pair, in one window.

A window is a stretch of weekly moves (3 years by default). Inside it, the (stock, factor) regression of C1
gives the label: would C1 write a SENSITIVE_TO (|t| >= 2 or partial r2 >= 0.05)? The features describe the
company's position in the graph:

- facts that do not change with time: the PRODUCES / CONSUMES links declared for the company or its
  industry, and whether it trades in Europe;
- its peers in that window: the other companies of its industry, its CORRELATED_WITH neighbours, the other
  members of its Louvain community and its 10 nearest companies by FastRP embedding. For each group, the
  share of peers exposed to the factor, their mean beta and how many there are.

A pair's own regression is only ever its label. Every peer feature averages the *other* companies, so the
model cannot read the answer from its input.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from scripts.ingest.prices import (
    MARKET_EUROPE,
    FactorSource,
    Listing,
    fit_sensitivity,
    residuals_by_isin,
    top_correlations,
)

NEAREST = 10  # embedding neighbours per company
PEER_GROUPS = ("industry", "neighbour", "community", "knn")
NUMERIC = ["produces", "consumes", "european"] + [
    f"{group}_{stat}" for group in PEER_GROUPS for stat in ("rate", "beta", "peers")
]
FIT_COLUMNS = ["isin", "factor", "beta", "t_stat", "r2", "label"]


@dataclass(frozen=True)
class Window:
    """A stretch of weeks, both ends included."""

    start: date
    end: date

    def cut(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The rows of a weekly frame that fall inside the window."""
        return frame[(frame.index >= pd.Timestamp(self.start)) & (frame.index <= pd.Timestamp(self.end))]


@dataclass(frozen=True)
class Positions:
    """Where each company sits in one window's correlation network: Louvain community, FastRP embedding."""

    communities: dict[str, int]
    embeddings: dict[str, list[float]]


@dataclass(frozen=True)
class Context:
    """What does not depend on the window: listings, industries, declared links and factors."""

    listings: list[Listing]
    companies: dict[str, str | None]  # every company of the graph -> its industry code
    declared: set[tuple[str, str, str]]  # (isin, factor code, "PRODUCES" or "CONSUMES")
    factors: list[FactorSource]

    @property
    def factor_codes(self) -> list[str]:
        """Factor codes in a fixed order (the one-hot columns follow it)."""
        return sorted(factor.code for factor in self.factors)

    def columns(self) -> list[str]:
        """Model inputs: the numeric features, then one column per factor."""
        return NUMERIC + [f"factor_{code}" for code in self.factor_codes]


@dataclass(frozen=True)
class WindowData:
    """Everything a window gives: every regression (with its label), the correlations and the positions."""

    window: Window
    fits: pd.DataFrame
    correlations: list[dict]
    positions: Positions


def window_fits(listings: list[Listing], stock_moves: pd.DataFrame, factor_moves: pd.DataFrame) -> pd.DataFrame:
    """Every usable (stock, factor) regression of the window, significant or not, with its 0/1 label."""
    rows = []
    for listing in listings:
        if listing.ticker not in stock_moves or listing.market not in stock_moves:
            continue
        for code in factor_moves.columns:
            fit = fit_sensitivity(stock_moves[listing.ticker], stock_moves[listing.market], factor_moves[code])
            if fit is not None:
                rows.append(
                    {
                        "isin": listing.isin,
                        "factor": code,
                        "beta": fit.beta,
                        "t_stat": fit.t_stat,
                        "r2": fit.partial_r2,
                        "label": int(fit.significant()),
                    }
                )
    return pd.DataFrame(rows, columns=FIT_COLUMNS)


def window_data(
    context: Context,
    window: Window,
    stock_moves: pd.DataFrame,
    factor_moves: pd.DataFrame,
    positions: Callable[[list[dict]], Positions],
) -> WindowData:
    """Regressions, residual correlations (same rule as C1) and network positions, all inside `window`."""
    stocks = window.cut(stock_moves)
    fits = window_fits(context.listings, stocks, window.cut(factor_moves))
    correlations = top_correlations(residuals_by_isin(context.listings, stocks))
    return WindowData(window, fits, correlations, positions(correlations))


def group_stats(pairs: pd.DataFrame, fits: pd.DataFrame, groups: dict, prefix: str) -> pd.DataFrame:
    """Peers = the other companies of the same group (industry or community), leave-one-out.

    The group totals include the company itself when it has a regression, so its own label and beta are
    subtracted before averaging.
    """
    members = fits.assign(group=fits["isin"].map(groups)).dropna(subset=["group"])
    totals = members.groupby(["group", "factor"]).agg(
        exposed=("label", "sum"), beta_sum=("beta", "sum"), count=("label", "size")
    )
    frame = pairs.assign(group=pairs["isin"].map(groups))
    frame = frame.merge(totals.reset_index(), on=["group", "factor"], how="left")
    own = fits[["isin", "factor", "beta", "label"]].rename(columns={"beta": "own_beta", "label": "own_label"})
    frame = frame.merge(own, on=["isin", "factor"], how="left")
    has_own = frame["own_label"].notna().astype(int)
    peers = (frame["count"].fillna(0) - has_own).clip(lower=0)  # a company with no group has no peers
    safe = peers.where(peers > 0)
    return pd.DataFrame(
        {
            f"{prefix}_rate": (frame["exposed"] - frame["own_label"].fillna(0)) / safe,
            f"{prefix}_beta": (frame["beta_sum"] - frame["own_beta"].fillna(0)) / safe,
            f"{prefix}_peers": peers,
        },
        index=pairs.index,
    )


def edge_stats(pairs: pd.DataFrame, fits: pd.DataFrame, edges: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Peers = the companies linked by `edges` (columns isin, peer; a company is never its own peer)."""
    peer_fits = fits[["isin", "factor", "beta", "label"]].rename(columns={"isin": "peer"})
    joined = edges.merge(peer_fits, on="peer")
    stats = joined.groupby(["isin", "factor"]).agg(
        rate=("label", "mean"), beta=("beta", "mean"), peers=("peer", "size")
    )
    frame = pairs[["isin", "factor"]].merge(stats.reset_index(), on=["isin", "factor"], how="left")
    return pd.DataFrame(
        {
            f"{prefix}_rate": frame["rate"].to_numpy(),
            f"{prefix}_beta": frame["beta"].to_numpy(),
            f"{prefix}_peers": frame["peers"].fillna(0).to_numpy(),
        },
        index=pairs.index,
    )


def neighbour_edges(correlations: list[dict]) -> pd.DataFrame:
    """CORRELATED_WITH pairs in both directions (they are stored once, a < b)."""
    if not correlations:
        return pd.DataFrame(columns=["isin", "peer"])
    pairs = pd.DataFrame(correlations)
    return pd.concat(
        [pairs.rename(columns={"a": "isin", "b": "peer"}), pairs.rename(columns={"b": "isin", "a": "peer"})]
    )[["isin", "peer"]]


def nearest_edges(embeddings: dict[str, list[float]], nearest: int = NEAREST) -> pd.DataFrame:
    """Each company's `nearest` most similar companies by cosine on the embeddings (positive cosine only)."""
    if len(embeddings) < 2:
        return pd.DataFrame(columns=["isin", "peer"])
    names = sorted(embeddings)
    vectors = np.array([embeddings[name] for name in names], dtype=float)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)
    cosine = unit @ unit.T
    np.fill_diagonal(cosine, -np.inf)
    rows = []
    for row, name in enumerate(names):
        for col in np.argsort(-cosine[row], kind="stable")[:nearest]:
            if cosine[row, col] > 0:
                rows.append({"isin": name, "peer": names[col]})
    return pd.DataFrame(rows, columns=["isin", "peer"])


def pair_features(context: Context, data: WindowData, pairs: pd.DataFrame) -> pd.DataFrame:
    """Features of the given (isin, factor) pairs from one window; NaN where a company has no such peer."""
    pairs = pairs[["isin", "factor"]].reset_index(drop=True)
    declared = context.declared
    european = {listing.isin: float(listing.market == MARKET_EUROPE) for listing in context.listings}
    keys = list(zip(pairs["isin"], pairs["factor"]))
    frame = pairs.assign(
        produces=[float((isin, factor, "PRODUCES") in declared) for isin, factor in keys],
        consumes=[float((isin, factor, "CONSUMES") in declared) for isin, factor in keys],
        european=pairs["isin"].map(european),
    )
    industries = {isin: industry for isin, industry in context.companies.items() if industry}
    blocks = [
        group_stats(pairs, data.fits, industries, "industry"),
        edge_stats(pairs, data.fits, neighbour_edges(data.correlations), "neighbour"),
        group_stats(pairs, data.fits, data.positions.communities, "community"),
        edge_stats(pairs, data.fits, nearest_edges(data.positions.embeddings), "knn"),
    ]
    frame = pd.concat([frame, *blocks], axis=1)
    for code in context.factor_codes:
        frame[f"factor_{code}"] = (frame["factor"] == code).astype(float)
    return frame


def labelled(context: Context, data: WindowData) -> pd.DataFrame:
    """Every regression of the window with its features and label: a training or test table."""
    features = pair_features(context, data, data.fits)
    return features.assign(label=data.fits["label"].to_numpy())


def direction(features: pd.DataFrame) -> np.ndarray:
    """'up' or 'down': sign of the peers' mean beta (correlation neighbours first, then community,
    embedding neighbours, industry). The model says *whether*; the peers say which way."""
    beta = features["neighbour_beta"]
    for column in ("community_beta", "knn_beta", "industry_beta"):
        beta = beta.fillna(features[column])
    return np.where(beta.fillna(0.0) < 0, "down", "up")
