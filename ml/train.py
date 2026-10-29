"""Train the sensitivity model and write PREDICTED_SENSITIVITY links (role C, C3). Usage: python -m ml.train

Question: from a stock's position in the graph (declared PRODUCES / CONSUMES, industry, correlation
neighbours, Louvain community, FastRP embedding), can we tell whether it is significantly exposed to a factor?

Temporal split: two consecutive windows of `returns_window_years` (3 years each, as C1). The model is fitted on
the older window, features and labels both from it, and evaluated on the recent window, which it never saw: no
price of the test period reaches training. It is compared with a naive baseline (industry share).

The 6 years of prices have their own cache (`data/cache/ml`). Yahoo refuses large bursts, so the download goes
in rounds: market proxies and factors first, then the stocks, again after a wait while a round still brings new
symbols. A run that cannot get the market proxies stops with an error instead of training on nothing.

The chosen model, fitted on the older window, then scores the recent window's pairs that have **no**
SENSITIVE_TO estimate (no significant beta, or no usable prices). Pairs with prob >= 0.5 get
`PREDICTED_SENSITIVITY {prob, direction, model_version}`, truth level `prediction`. The model and the metrics
go to `ml/artifacts/` (git-ignored); the numbers of the report come from `metrics.json`.
"""

import json
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import joblib
import pandas as pd

from app.config import Settings, get_settings
from app.database.neo4j import Neo4jClient
from ml.features import Context, Positions, Window, WindowData, direction, labelled, pair_features, window_data
from ml.models import candidates, choose, evaluate, importances, industry_baseline
from ml.network import network_positions
from scripts.ingest import prices

PROB_MIN = 0.5
DOWNLOAD_ROUNDS = 8
ROUND_WAIT = 120  # seconds between download rounds, for Yahoo's rate limit to clear
MAX_ROWS = 100_000
ARTIFACTS = Path(__file__).parent / "artifacts"

COMPANIES = """
MATCH (c:Company)
OPTIONAL MATCH (c)-[:IN_INDUSTRY]->(i:Industry)
RETURN c.isin AS isin, i.code AS industry
ORDER BY isin
LIMIT $limit
"""

# Declared on the company itself, or on its industry (most PRODUCES / CONSUMES are industry-level facts).
DECLARED = """
MATCH (c:Company)-[r:PRODUCES|CONSUMES]->(x:Factor)
RETURN c.isin AS isin, x.code AS factor, type(r) AS relation
LIMIT $limit
UNION
MATCH (c:Company)-[:IN_INDUSTRY]->(:Industry)-[r:PRODUCES|CONSUMES]->(x:Factor)
RETURN c.isin AS isin, x.code AS factor, type(r) AS relation
LIMIT $limit
"""

ESTIMATED = "MATCH (c:Company)-[:SENSITIVE_TO]->(x:Factor) RETURN c.isin AS isin, x.code AS factor LIMIT $limit"

# One statement, so one transaction: the old links are deleted and the new ones created together. If any row
# fails, Neo4j rolls everything back and the previous predictions stay as they were.
REPLACE = """
CALL {
    MATCH (c:Company)-[p:PREDICTED_SENSITIVITY]->(:Factor) WHERE c.isin IN $isins
    DELETE p
}
UNWIND $rows AS row
MATCH (c:Company {isin: row.isin})
MATCH (x:Factor {code: row.factor})
CREATE (c)-[:PREDICTED_SENSITIVITY {prob: row.prob, direction: row.direction, model_version: row.model_version}]->(x)
RETURN count(*) AS written
"""


def load_context(client: Neo4jClient) -> Context:
    """Listings and factors (as C1 reads them), every company's industry, and the declared links."""
    companies = {row["isin"]: row["industry"] for row in client.read(COMPANIES, {"limit": MAX_ROWS})}
    declared = {(row["isin"], row["factor"], row["relation"]) for row in client.read(DECLARED, {"limit": MAX_ROWS})}
    return Context(prices.load_listings(client), companies, declared, prices.load_factors(client))


def load_estimated(client: Neo4jClient) -> set[tuple[str, str]]:
    """(isin, factor) pairs that already have a SENSITIVE_TO estimate: they never get a prediction."""
    return {(row["isin"], row["factor"]) for row in client.read(ESTIMATED, {"limit": MAX_ROWS})}


def split_windows(end: date, days: int) -> tuple[Window, Window]:
    """Two consecutive windows of `days` days ending at `end`: (older, recent). No week is in both."""
    recent_start = end - timedelta(days=days - 1)
    older = Window(recent_start - timedelta(days=days), recent_start - timedelta(days=1))
    return older, Window(recent_start, end)


def window_days(settings: Settings) -> int:
    """Each window is as long as C1's history."""
    return 365 * settings.returns_window_years


def with_data(frame: pd.DataFrame) -> set[str]:
    """Columns that hold at least one value."""
    return set(frame.columns[frame.notna().any()])


def load_moves(
    context: Context, settings: Settings, wait: Callable[[float], None] = time.sleep
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly moves over both windows (6 years), downloaded in rounds until a round brings nothing new."""
    start = date.today() - timedelta(days=2 * window_days(settings) + 7)
    cache_dir = Path(settings.data_dir) / "cache" / "ml"
    known: set[str] | None = None
    stock_moves = factor_moves = pd.DataFrame()
    for attempt in range(DOWNLOAD_ROUNDS):
        if attempt:
            wait(ROUND_WAIT)
        # Market proxies and factors alone first: one small request, and without them nothing can be fitted.
        prices.weekly_moves([], context.factors, settings, start, cache_dir=cache_dir)
        stock_moves, factor_moves = prices.weekly_moves(
            context.listings, context.factors, settings, start, cache_dir=cache_dir
        )
        found = with_data(stock_moves) | with_data(factor_moves)
        print(f"download round {attempt + 1}: {len(found)} series with data", flush=True)
        if found == known:
            break
        known = found
    missing = {prices.MARKET_EUROPE, prices.MARKET_WORLD} - with_data(stock_moves)
    if missing:
        raise RuntimeError(f"no prices for the market proxies {sorted(missing)}: Yahoo still refuses, retry later")
    return stock_moves, factor_moves


def write_predictions(client: Neo4jClient, isins: list[str], rows: list[dict]) -> int:
    """Replace the PREDICTED_SENSITIVITY links of `isins` by `rows` in one transaction; return how many were written.

    All or nothing: a failure keeps the previous predictions. The rows fit in one transaction (676 on the dev
    database, at most one per company and factor).
    """
    result = client.write(REPLACE, {"isins": isins, "rows": rows})
    return result[0]["written"] if result else 0


def check_labels(**tables: pd.DataFrame) -> None:
    """Both windows need exposed and unexposed pairs, or there is nothing to learn or to measure."""
    for name, table in tables.items():
        if table.empty or table["label"].nunique() < 2:
            raise ValueError(f"the {name} window has {len(table)} pairs and only one class: not enough prices")


def candidates_to_score(context: Context, estimated: set[tuple[str, str]]) -> pd.DataFrame:
    """Every (company, factor) pair of the graph without an estimate."""
    pairs = [
        {"isin": isin, "factor": code}
        for isin in sorted(context.companies)
        for code in context.factor_codes
        if (isin, code) not in estimated
    ]
    return pd.DataFrame(pairs, columns=["isin", "factor"])


def both_windows(
    context: Context,
    moves: tuple[pd.DataFrame, pd.DataFrame],
    positions: Callable[[list[dict]], Positions],
    days: int,
) -> tuple[WindowData, WindowData]:
    """The older and the recent window of the weekly moves, each with its own regressions and network."""
    stock_moves, factor_moves = moves
    older, recent = split_windows(stock_moves.index[-1].date(), days)
    return (
        window_data(context, older, stock_moves, factor_moves, positions),
        window_data(context, recent, stock_moves, factor_moves, positions),
    )


def compare(context: Context, train: pd.DataFrame, test: pd.DataFrame) -> tuple[dict, dict]:
    """Test-window metrics of the baseline and of each model fitted on the training window."""
    columns = context.columns()
    metrics = {"baseline": evaluate(test["label"], industry_baseline(train, test, context.companies))}
    fitted = {}
    for name, model in candidates().items():
        fitted[name] = model.fit(train[columns], train["label"])
        metrics[name] = evaluate(test["label"], model.predict_proba(test[columns])[:, 1])
    return metrics, fitted


def prediction_rows(model, features: pd.DataFrame, columns: list[str], version: str) -> list[dict]:
    """Pairs the model scores at PROB_MIN or more, with the direction their peers give."""
    if features.empty:
        return []
    scored = features.assign(prob=model.predict_proba(features[columns])[:, 1], direction=direction(features))
    return [
        {
            "isin": row.isin,
            "factor": row.factor,
            "prob": round(float(row.prob), 4),
            "direction": row.direction,
            "model_version": version,
        }
        for row in scored[scored["prob"] >= PROB_MIN].itertuples()
    ]


def run(
    client: Neo4jClient,
    settings: Settings,
    moves: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    positions: Callable[[list[dict]], Positions] | None = None,
    artifacts: Path = ARTIFACTS,
) -> dict:
    """Build both windows, choose and evaluate the model, write the predictions; return the report.

    `moves` and `positions` replace the downloads and GDS in tests.
    """
    context = load_context(client)
    older_data, recent_data = both_windows(
        context,
        moves or load_moves(context, settings),
        positions or (lambda pairs: network_positions(client, pairs)),
        window_days(settings),
    )
    train, test = labelled(context, older_data), labelled(context, recent_data)
    check_labels(train=train, test=test)

    chosen = choose(train, context.columns())
    metrics, fitted = compare(context, train, test)
    model = fitted[chosen]
    version = f"{chosen}-{recent_data.window.end.isoformat()}"
    pairs = candidates_to_score(context, load_estimated(client))
    rows = prediction_rows(model, pair_features(context, recent_data, pairs), context.columns(), version)
    report = {
        "windows": {
            "train": [older_data.window.start, older_data.window.end],
            "test": [recent_data.window.start, recent_data.window.end],
        },
        "pairs": {"train": len(train), "test": len(test)},
        "exposed_share": {
            "train": round(float(train["label"].mean()), 4),
            "test": round(float(test["label"].mean()), 4),
        },
        "metrics": metrics,
        "chosen": chosen,
        "model_version": version,
        "importances": importances(model, test, context.columns()),
        "scored_without_estimate": len(pairs),
        "predictions_written": write_predictions(client, sorted(context.companies), rows),
    }
    save(artifacts, model, report)
    return report


def save(artifacts: Path, model, report: dict) -> None:
    """The fitted model and the report, for the notebook and docs/ml_report.md."""
    artifacts.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, artifacts / "model.joblib")
    (artifacts / "metrics.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def main() -> None:
    """Run on the dev database and print the report."""
    settings = get_settings()
    client = Neo4jClient.from_settings(settings)
    try:
        print(json.dumps(run(client, settings), indent=2, default=str))
    finally:
        client.close()


if __name__ == "__main__":
    main()
