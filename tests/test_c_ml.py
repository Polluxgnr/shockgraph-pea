"""C3: features, temporal split, models and predictions (synthetic prices), GDS positions and reads (mini graph)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.config import Settings
from app.services.gds import GdsService
from ml import train
from ml.features import (
    Context,
    Positions,
    Window,
    WindowData,
    direction,
    edge_stats,
    group_stats,
    labelled,
    nearest_edges,
    neighbour_edges,
    pair_features,
    window_data,
)
from ml.models import candidates, evaluate, industry_baseline, precision_at_k, validation_split
from ml.network import GRAPH_NAME, network_positions
from scripts.ingest.prices import MARKET_EUROPE, FactorSource, Listing
from tests.conftest import FakeClient

AUTOROUTE, VARENNE = "DETEST000060", "FRTEST000037"
MINI_COMMUNITIES = [
    {"FRTEST000011", "DETEST000078", "DETEST000060", "FRTEST000052"},
    {"FRTEST000029", "FRTEST000086"},
    {"FRTEST000037", "NLTEST000045", "USTEST000094"},
]

# Synthetic universe: industry -> (factor it moves with, beta). BANKS moves with nothing.
INDUSTRIES = {"ENERGY": ("OIL", 0.5), "AIRLINES": ("OIL", -0.4), "EXPORT": ("FX", -0.8), "BANKS": (None, 0.0)}
PER_INDUSTRY = 10
LONERS = (0, 9)  # one exposed, one not: no industry co-movement, so no correlation neighbours
FACTORS = [FactorSource("OIL", "yahoo", "OIL=F", "pct"), FactorSource("FX", "yahoo", "FX=X", "pct")]
UNPRICED = {"ZZ00UNPRICED1": "ENERGY", "ZZ00UNPRICED2": "AIRLINES", "ZZ00UNPRICED3": "BANKS"}
SETTINGS = Settings(returns_window_years=2)  # 4 synthetic years: two windows of 2 years (~104 weeks)


def isin_of(industry: str, number: int) -> str:
    """Synthetic ISIN, e.g. ZZENERGY0003."""
    return f"ZZ{industry[:6]:<6}{number:04d}".replace(" ", "X")


def synthetic_universe(weeks: int = 208, seed: int = 7) -> tuple[Context, pd.DataFrame, pd.DataFrame]:
    """40 stocks over 4 years: 8 of 10 per exposed industry really move with their factor."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(end=pd.Timestamp("2026-10-23"), periods=weeks, freq="W-FRI")
    market = rng.normal(0, 2, weeks)
    factor_moves = pd.DataFrame({"OIL": rng.normal(0, 4, weeks), "FX": rng.normal(0, 1.5, weeks)}, index=index)
    stocks = {MARKET_EUROPE: market}
    listings, companies = [], {}
    for industry, (factor, beta) in INDUSTRIES.items():
        common = rng.normal(0, 2, weeks)
        for number in range(PER_INDUSTRY):
            isin = isin_of(industry, number)
            exposure = beta if factor and number < 8 else 0.0
            shared = 0.0 if number in LONERS else 1.0
            move = market + shared * common + rng.normal(0, 1.5, weeks)
            if factor:
                move = move + exposure * factor_moves[factor].to_numpy()
            stocks[isin] = move
            listings.append(Listing(isin, isin, MARKET_EUROPE))
            companies[isin] = industry
    companies.update(UNPRICED)
    energy = [isin_of("ENERGY", number) for number in range(PER_INDUSTRY)] + ["ZZ00UNPRICED1"]
    declared = {(isin, "OIL", "PRODUCES") for isin in energy}  # an industry-level fact, as in the real graph
    context = Context(listings, companies, declared, FACTORS)
    return context, pd.DataFrame(stocks, index=index), factor_moves


def component_positions(pairs: list[dict]) -> Positions:
    """Stand-in for GDS: community = connected component, embedding = one-hot of the component."""
    links: dict[str, set[str]] = {}
    for pair in pairs:
        links.setdefault(pair["a"], set()).add(pair["b"])
        links.setdefault(pair["b"], set()).add(pair["a"])
    communities: dict[str, int] = {}
    for start in sorted(links):
        if start in communities:
            continue
        label, stack = len(set(communities.values())), [start]
        while stack:
            node = stack.pop()
            if node not in communities:
                communities[node] = label
                stack.extend(links[node] - set(communities))
    size = max(communities.values(), default=-1) + 1
    embeddings = {isin: [float(label == slot) for slot in range(size)] for isin, label in communities.items()}
    return Positions(communities, embeddings)


@pytest.fixture(scope="module")
def universe():
    """The synthetic universe and its two windows."""
    context, stocks, factors = synthetic_universe()
    older, recent = train.split_windows(stocks.index[-1].date(), train.window_days(SETTINGS))
    return context, stocks, factors, older, recent


# --- windows and labels -----------------------------------------------------------------------------


def test_windows_are_two_full_c1_windows():
    """Two windows of 3 years each, like C1's; the older one ends the day before the recent one starts."""
    days = train.window_days(Settings(returns_window_years=3))
    older, recent = train.split_windows(date(2026, 10, 23), days)
    assert days == 3 * 365
    assert older.end < recent.start
    assert (recent.start - older.end).days == 1
    assert (recent.end - recent.start).days == (older.end - older.start).days == days - 1


def fake_weekly_moves(rounds: list[set[str]]):
    """weekly_moves stand-in: each full call (with listings) returns the next round's symbols with data."""
    calls: list[int] = []

    def weekly_moves(listings, factors, settings, start, cache_dir=None):
        del factors, settings
        calls.append(len(listings))
        assert cache_dir.parts[-2:] == ("cache", "ml") and start < date.today()
        found = rounds[min(sum(1 for count in calls if count), len(rounds)) - 1]
        columns = ["EXSA.DE", "URTH", "AAA", "BBB"]
        return pd.DataFrame({name: [1.0 if name in found else None] for name in columns}), pd.DataFrame({"OIL": [1.0]})

    return weekly_moves, calls


def test_download_goes_in_rounds_until_nothing_new(monkeypatch, tmp_path):
    """Round 1 gets AAA only, round 2 the markets too, round 3 nothing new: stop, with a wait between rounds.
    Markets and factors are asked alone (no listings) before each round."""
    context, *_ = synthetic_universe(weeks=60)
    full = {"EXSA.DE", "URTH", "AAA"}
    weekly_moves, calls = fake_weekly_moves([{"AAA"}, full, full])
    monkeypatch.setattr(train.prices, "weekly_moves", weekly_moves)
    waits: list[float] = []
    stocks, _ = train.load_moves(context, Settings(data_dir=str(tmp_path)), wait=waits.append)
    assert train.with_data(stocks) == full
    assert waits == [train.ROUND_WAIT] * 2
    assert calls[0] == 0 and calls[1] == len(context.listings)


def test_download_without_market_proxies_stops_with_an_error(monkeypatch, tmp_path):
    """Yahoo never gives the markets: a clear error instead of training on nothing."""
    context, *_ = synthetic_universe(weeks=60)
    weekly_moves, _ = fake_weekly_moves([{"AAA"}])
    monkeypatch.setattr(train.prices, "weekly_moves", weekly_moves)
    with pytest.raises(RuntimeError, match="market proxies"):
        train.load_moves(context, Settings(data_dir=str(tmp_path)), wait=lambda seconds: None)


def test_window_cut_keeps_only_its_weeks(universe):
    """Each window sees its own ~104 weeks and nothing of the other."""
    _, stocks, _, older, recent = universe
    first, second = older.cut(stocks), recent.cut(stocks)
    assert first.index.max() < second.index.min()
    assert len(first) + len(second) == len(stocks)
    assert 100 <= len(second) <= 106


def test_labels_find_the_stocks_that_really_move_with_their_factor(universe):
    """In the recent window, 8 of 10 energy stocks are exposed to OIL; banks are (almost) never exposed."""
    context, stocks, factors, _, recent = universe
    fits = window_data(context, recent, stocks, factors, component_positions).fits
    energy = fits[fits["isin"].str.startswith("ZZENERGY") & (fits["factor"] == "OIL")]
    assert energy.sort_values("isin")["label"].tolist()[:8] == [1] * 8
    banks = fits[fits["isin"].str.startswith("ZZBANKS")]
    assert banks["label"].mean() <= 0.15  # |t| >= 2 by chance about 5 % of the time


# --- features: never the pair's own answer -------------------------------------------------------------


def hand_fits() -> pd.DataFrame:
    """Three companies of one industry; only A is exposed."""
    return pd.DataFrame(
        {
            "isin": ["A", "B", "C"],
            "factor": ["OIL"] * 3,
            "beta": [0.9, 0.1, 0.0],
            "t_stat": [5.0, 0.5, 0.0],
            "r2": [0.3, 0.0, 0.0],
            "label": [1, 0, 0],
        }
    )


def test_group_features_leave_the_company_itself_out():
    """A's industry rate is 0 (its peers B and C are not exposed); B sees 1 exposed peer out of 2."""
    fits = hand_fits()
    pairs = pd.DataFrame({"isin": ["A", "B", "D"], "factor": ["OIL"] * 3})
    stats = group_stats(pairs, fits, {"A": "X", "B": "X", "C": "X", "D": "X"}, "industry")
    assert stats["industry_rate"].tolist()[:2] == [0.0, 0.5]
    assert stats["industry_beta"].tolist()[:2] == pytest.approx([0.05, 0.45])
    assert stats["industry_peers"].tolist() == [2, 2, 3]  # D has no regression: all 3 are its peers
    assert stats["industry_rate"].iloc[2] == pytest.approx(1 / 3)


def test_a_company_without_group_has_no_peers():
    """No industry: NaN rate, 0 peers (never a negative count)."""
    stats = group_stats(pd.DataFrame({"isin": ["A"], "factor": ["OIL"]}), hand_fits(), {}, "industry")
    assert np.isnan(stats["industry_rate"].iloc[0])
    assert stats["industry_peers"].iloc[0] == 0


def test_neighbour_features_average_the_linked_companies():
    """A is linked to B and C: rate 0, mean beta 0.05. B only to A: rate 1."""
    edges = neighbour_edges([{"a": "A", "b": "B", "rho": 0.6}, {"a": "A", "b": "C", "rho": 0.55}])
    pairs = pd.DataFrame({"isin": ["A", "B", "Z"], "factor": ["OIL"] * 3})
    stats = edge_stats(pairs, hand_fits(), edges, "neighbour")
    assert stats["neighbour_rate"].tolist()[:2] == [0.0, 1.0]
    assert stats["neighbour_beta"].iloc[0] == pytest.approx(0.05)
    assert stats["neighbour_peers"].tolist() == [2, 1, 0]
    assert np.isnan(stats["neighbour_rate"].iloc[2])


def test_nearest_edges_skip_the_company_itself_and_opposite_positions():
    """Cosine > 0 only, never the company itself."""
    edges = nearest_edges({"A": [1.0, 0.0], "B": [0.9, 0.1], "C": [-1.0, 0.0]}, nearest=2)
    assert set(map(tuple, edges.to_numpy())) == {("A", "B"), ("B", "A")}
    assert nearest_edges({"A": [1.0]}).empty


def test_pair_features_declared_links_and_factor_columns(universe):
    """Energy produces OIL (declared); one-hot factor columns; the column list matches the model inputs."""
    context, stocks, factors, _, recent = universe
    data = window_data(context, recent, stocks, factors, component_positions)
    pairs = pd.DataFrame({"isin": [isin_of("ENERGY", 0), isin_of("BANKS", 0)], "factor": ["OIL", "FX"]})
    features = pair_features(context, data, pairs)
    assert features["produces"].tolist() == [1.0, 0.0]
    assert features["factor_OIL"].tolist() == [1.0, 0.0]
    assert features["factor_FX"].tolist() == [0.0, 1.0]
    assert set(context.columns()) <= set(features.columns)


def test_direction_follows_the_peers_beta():
    """Neighbours first; when a company has none, the next group decides; nothing at all gives 'up'."""
    features = pd.DataFrame(
        {
            "neighbour_beta": [-0.3, np.nan, np.nan],
            "community_beta": [0.5, np.nan, np.nan],
            "knn_beta": [np.nan, -0.2, np.nan],
            "industry_beta": [0.1, 0.4, np.nan],
        }
    )
    assert direction(features).tolist() == ["down", "down", "up"]


# --- baseline and metrics ------------------------------------------------------------------------------


def test_baseline_uses_the_training_window_only():
    """Industry share from the training table; an unknown industry falls back to the factor share."""
    train_table = pd.DataFrame({"isin": ["A", "B", "C", "D"], "factor": ["OIL"] * 4, "label": [1, 1, 0, 0]})
    industries = {"A": "X", "B": "X", "C": "Y", "D": "Y", "E": "X", "F": "NEW"}
    pairs = pd.DataFrame({"isin": ["E", "C", "F"], "factor": ["OIL"] * 3})
    assert industry_baseline(train_table, pairs, industries).tolist() == [1.0, 0.0, 0.5]


def test_precision_at_k_and_evaluate():
    """Top 2 scores hold one exposed pair: 0.5. k larger than the table uses every pair."""
    labels, scores = np.array([1, 0, 0, 1]), np.array([0.9, 0.8, 0.1, 0.2])
    assert precision_at_k(labels, scores, 2) == 0.5
    assert precision_at_k(labels, scores, 10) == 0.5
    report = evaluate(labels, scores, cutoffs=(1, 2))
    assert report["precision_at"] == {1: 1.0, 2: 0.5}
    assert 0 < report["auc_pr"] <= 1


# --- end to end ------------------------------------------------------------------------------------------


class MLClient:
    """Answers the reads of ml.train from the synthetic universe and records the writes."""

    def __init__(self, context: Context, estimated: set[tuple[str, str]]) -> None:
        self.context = context
        self.estimated = estimated
        self.writes: list[tuple[str, dict]] = []

    def read(self, query: str, params: dict | None = None) -> list[dict]:
        """Rows for the query, recognised by its pattern."""
        del params
        if "c.ticker IS NOT NULL" in query:
            return [{"isin": item.isin, "ticker": item.ticker, "currency": "EUR"} for item in self.context.listings]
        if "MATCH (x:Factor)" in query:
            return [
                {"code": item.code, "kind": "commodity", "source": "yahoo", "source_id": item.source_id}
                for item in self.context.factors
            ]
        if "PRODUCES|CONSUMES" in query:
            return [{"isin": isin, "factor": code, "relation": rel} for isin, code, rel in self.context.declared]
        if "SENSITIVE_TO" in query:
            return [{"isin": isin, "factor": code} for isin, code in self.estimated]
        return [{"isin": isin, "industry": industry} for isin, industry in self.context.companies.items()]

    def write(self, query: str, params: dict | None = None) -> list[dict]:
        """Record the write and pretend every row was written."""
        self.writes.append((query, params or {}))
        return [{"written": len((params or {}).get("rows", []))}]


@pytest.fixture(scope="module")
def trained(universe, tmp_path_factory):
    """One full run: train on the older window, test on the recent one, score the pairs without estimate."""
    context, stocks, factors, _, recent = universe
    fits = window_data(context, recent, stocks, factors, component_positions).fits
    estimated = {(row.isin, row.factor) for row in fits[fits["label"] == 1].itertuples()}
    client = MLClient(context, estimated)
    artifacts = tmp_path_factory.mktemp("artifacts")
    report = train.run(client, SETTINGS, moves=(stocks, factors), positions=component_positions, artifacts=artifacts)
    written = [row for query, params in client.writes if "CREATE" in query for row in params["rows"]]
    return report, written, estimated, client, artifacts


def test_run_trains_on_the_older_window_and_tests_on_the_recent_one(trained):
    """The report gives both windows, the three ways of ranking, and the chosen model."""
    report, *_ = trained
    train_window, test_window = report["windows"]["train"], report["windows"]["test"]
    assert train_window[1] < test_window[0]
    assert set(report["metrics"]) == {"baseline", "logistic", "gradient_boosting"}
    assert report["chosen"] in ("logistic", "gradient_boosting")
    assert report["model_version"] == f"{report['chosen']}-2026-10-23"
    assert report["pairs"]["train"] == report["pairs"]["test"] == 80  # 40 stocks x 2 factors


def test_models_rank_exposed_pairs_far_above_chance(trained):
    """On synthetic data with a clear structure, AUC-PR is well above the share of exposed pairs."""
    report, *_ = trained
    share = report["exposed_share"]["test"]
    for name in ("baseline", "logistic", "gradient_boosting"):
        assert report["metrics"][name]["auc_pr"] > share + 0.3, name


def test_predictions_are_only_written_where_no_estimate_exists(trained):
    """No prediction on a pair C1 already estimated; every prob >= 0.5 with a direction and a version."""
    report, written, estimated, _, _ = trained
    assert written and report["predictions_written"] == len(written)
    assert not {(row["isin"], row["factor"]) for row in written} & estimated
    assert all(0.5 <= row["prob"] <= 1 and row["direction"] in ("up", "down") for row in written)
    assert {row["model_version"] for row in written} == {report["model_version"]}


def test_a_company_without_prices_is_ranked_by_its_industry(universe):
    """No ticker: no regression, no neighbour, no community. Its industry and declared links still rank it
    above an unpriced bank on OIL, and its industry peers give the sign. It stays below 0.5: in training,
    a stock without neighbours was rarely exposed, so the model is not confident on industry alone."""
    context, stocks, factors, older, recent = universe
    table = labelled(context, window_data(context, older, stocks, factors, component_positions))
    features = pair_features(
        context,
        window_data(context, recent, stocks, factors, component_positions),
        pd.DataFrame({"isin": sorted(UNPRICED), "factor": ["OIL"] * 3}),
    )
    columns = context.columns()
    for name, model in candidates().items():
        energy, airline, bank = model.fit(table[columns], table["label"]).predict_proba(features[columns])[:, 1]
        assert energy > bank and airline > bank, name
    assert direction(features).tolist()[:2] == ["up", "down"]


def test_rerun_replaces_old_predictions_first(trained):
    """The first write deletes the previous predictions of every company of the universe."""
    _, _, _, client, _ = trained
    query, params = next(write for write in client.writes if "PREDICTED_SENSITIVITY" in write[0])
    assert "DELETE p" in query
    assert "ZZ00UNPRICED1" in params["isins"]


def test_run_saves_the_model_and_the_metrics(trained):
    """model.joblib and metrics.json in the artifacts folder; importances name real features."""
    report, _, _, _, artifacts = trained
    assert (artifacts / "model.joblib").exists()
    assert "auc_pr" in (artifacts / "metrics.json").read_text(encoding="utf-8")
    assert report["importances"] and all("feature" in item for item in report["importances"])


def test_one_class_window_is_refused():
    """Not enough prices: a clear error instead of a model trained on nothing."""
    with pytest.raises(ValueError, match="only one class"):
        train.check_labels(train=pd.DataFrame({"label": [0, 0]}), test=pd.DataFrame({"label": [0, 1]}))


def test_labelled_table_has_one_row_per_regression(universe):
    """The training table keeps every regression, exposed or not, in the same order as the fits."""
    context, stocks, factors, older, _ = universe
    data = window_data(context, older, stocks, factors, component_positions)
    table = labelled(context, data)
    assert len(table) == len(data.fits)
    assert table["label"].tolist() == data.fits["label"].tolist()


# --- GDS on the mini graph --------------------------------------------------------------------------------


def test_network_positions_without_pairs_does_not_touch_the_database():
    """An empty window gives empty positions and no query."""
    client = FakeClient()
    assert network_positions(client, []) == Positions({}, {})
    assert not client.queries


@pytest.mark.neo4j
@pytest.mark.gds
def test_network_positions_match_c2_on_the_mini_graph(neo4j_client):
    """Same three communities as C2's refresh, 64 numbers per company, and the projection is dropped."""
    if not neo4j_client.has_gds():
        pytest.skip("GDS plugin not installed in the test database")
    pairs = neo4j_client.read(
        "MATCH (a:Company)-[r:CORRELATED_WITH]->(b:Company) RETURN a.isin AS a, b.isin AS b, r.rho AS rho LIMIT 100"
    )
    positions = network_positions(neo4j_client, pairs)
    for group in MINI_COMMUNITIES:
        assert len({positions.communities[isin] for isin in group}) == 1, group
    assert len(set(positions.communities.values())) == 3
    assert {len(vector) for vector in positions.embeddings.values()} == {64}
    exists = neo4j_client.read("CALL gds.graph.exists($graph) YIELD exists RETURN exists", {"graph": GRAPH_NAME})
    assert exists[0]["exists"] is False


# --- reads and writes on the mini graph -------------------------------------------------------------------


@pytest.mark.neo4j
def test_predicted_exposures_of_autoroute(neo4j_client):
    """Fixture: Autoroute -> EURUSD, prob 0.71, model fixture-v0, truth prediction."""
    exposures = GdsService(neo4j_client).predicted_exposures(AUTOROUTE)
    assert [(item.factor_code, item.prob, item.source, item.truth) for item in exposures] == [
        ("EURUSD", 0.71, "fixture-v0", "prediction")
    ]
    assert exposures[0].relation == "PREDICTED_SENSITIVITY"
    assert exposures[0].company_name.startswith("Autoroute")


@pytest.mark.neo4j
def test_predicted_exposures_unknown_and_empty(neo4j_client):
    """Unknown ISIN: None (404). Known company without prediction: empty list."""
    service = GdsService(neo4j_client)
    assert service.predicted_exposures("XX0000000000") is None
    assert service.predicted_exposures(VARENNE) == []


@pytest.mark.neo4j
def test_predicted_exposures_endpoint(api):
    """GET /api/companies/{isin}/predicted-exposures returns the fixture prediction; unknown is 404."""
    response = api.get(f"/api/companies/{AUTOROUTE}/predicted-exposures")
    assert response.status_code == 200
    assert response.json()[0]["prob"] == 0.71
    assert api.get("/api/companies/XX0000000000/predicted-exposures").status_code == 404


@pytest.mark.neo4j
def test_context_and_estimates_from_the_mini_graph(neo4j_client):
    """Petrolia produces BRENT through its industry and already has a BRENT estimate."""
    context = train.load_context(neo4j_client)
    assert ("FRTEST000011", "BRENT", "PRODUCES") in context.declared
    assert context.companies["FRTEST000011"]
    assert ("FRTEST000011", "BRENT") in train.load_estimated(neo4j_client)


@pytest.mark.neo4j
def test_write_predictions_replaces_and_cleans_up(neo4j_client):
    """Writes on two ZZ companies, a second write replaces the first, then everything is removed."""
    isins = ["ZZTESTC30001", "ZZTESTC30002"]
    neo4j_client.write("UNWIND $isins AS isin MERGE (:Company {isin: isin, name: 'Test ' + isin})", {"isins": isins})
    try:
        row = {"isin": isins[0], "factor": "BRENT", "prob": 0.8, "direction": "up", "model_version": "test-v1"}
        assert train.write_predictions(neo4j_client, isins, [row, {**row, "isin": isins[1], "factor": "COPPER"}]) == 2
        assert train.write_predictions(neo4j_client, isins, [{**row, "prob": 0.6}]) == 1
        found = GdsService(neo4j_client).predicted_exposures(isins[0])
        assert [(item.factor_code, item.prob) for item in found] == [("BRENT", 0.6)]
        assert GdsService(neo4j_client).predicted_exposures(isins[1]) == []
    finally:
        neo4j_client.write("MATCH (c:Company) WHERE c.isin IN $isins DETACH DELETE c", {"isins": isins})


@pytest.mark.neo4j
def test_a_failed_write_keeps_the_previous_predictions(neo4j_client):
    """A bad row in the middle rolls the whole replacement back: the old links are neither deleted nor mixed."""
    isins = ["ZZTESTC30003", "ZZTESTC30004"]
    neo4j_client.write("UNWIND $isins AS isin MERGE (:Company {isin: isin, name: 'Test ' + isin})", {"isins": isins})
    try:
        row = {"isin": isins[0], "factor": "BRENT", "prob": 0.8, "direction": "up", "model_version": "test-v1"}
        assert train.write_predictions(neo4j_client, isins, [row, {**row, "isin": isins[1], "factor": "COPPER"}]) == 2
        new = {**row, "prob": 0.6, "model_version": "test-v2"}
        broken = {**new, "isin": isins[1], "prob": {"not": "a number"}}  # a map is not a valid property value
        with pytest.raises(Exception):
            train.write_predictions(neo4j_client, isins, [new, broken])
        service = GdsService(neo4j_client)
        assert [(item.factor_code, item.prob) for item in service.predicted_exposures(isins[0])] == [("BRENT", 0.8)]
        assert [item.factor_code for item in service.predicted_exposures(isins[1])] == ["COPPER"]
    finally:
        neo4j_client.write("MATCH (c:Company) WHERE c.isin IN $isins DETACH DELETE c", {"isins": isins})


def labelled_table(exposed: set[str], companies: int = 12) -> pd.DataFrame:
    """Three pairs per company; every pair of the companies in `exposed` is exposed."""
    isins = [f"ZZ{index:010d}" for index in range(companies)]
    return pd.DataFrame(
        [{"isin": isin, "factor": code, "label": int(isin in exposed)} for isin in isins for code in ("A", "B", "C")]
    )


def test_validation_split_has_both_classes_on_each_side():
    """Few exposed companies: the split chosen still has exposed and unexposed pairs in fit and in check."""
    table = labelled_table({"ZZ0000000001", "ZZ0000000007"})
    fit, check = validation_split(table)
    assert fit["label"].nunique() == 2 and check["label"].nunique() == 2
    assert not set(fit["isin"]) & set(check["isin"])
    assert validation_split(table)[1].equals(check)  # deterministic


def test_validation_split_with_one_exposed_company_is_refused():
    """One exposed company cannot be on both sides: an explicit error, not a crash inside scikit-learn."""
    with pytest.raises(ValueError, match="too few exposed companies"):
        validation_split(labelled_table({"ZZ0000000003"}))


def test_window_data_with_a_window_outside_the_prices(universe):
    """A window with no weeks gives no regression and no correlation, without failing."""
    context, stocks, factors, _, _ = universe
    data = window_data(context, Window(date(2000, 1, 7), date(2000, 12, 29)), stocks, factors, component_positions)
    assert isinstance(data, WindowData)
    assert data.fits.empty and data.correlations == []
