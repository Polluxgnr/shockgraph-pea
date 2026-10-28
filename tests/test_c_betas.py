"""C1: betas and residual correlations on synthetic series with known answers (no network)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.config import Settings
from scripts.ingest import prices
from scripts.ingest.prices import FactorSource, Listing

WEEKS = 156
ZZ_ISINS = ["ZZTEST0000C1", "ZZTEST0000C2", "ZZTEST0000C3"]


def weeks(count: int = WEEKS) -> pd.DatetimeIndex:
    """Consecutive Fridays."""
    return pd.date_range("2023-10-20", periods=count, freq="W-FRI")


def synthetic(beta: float, seed: int = 7, count: int = WEEKS) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Stock = 0.1 + 0.9 * market + beta * factor + noise, with a factor correlated to the market."""
    rng = np.random.default_rng(seed)
    index = weeks(count)
    market = pd.Series(rng.normal(0, 2.0, count), index=index)
    factor = pd.Series(0.3 * market.to_numpy() + rng.normal(0, 3.0, count), index=index)
    stock = 0.1 + 0.9 * market + beta * factor + rng.normal(0, 1.5, count)
    return stock, market, factor


# --- Regression --------------------------------------------------------------------------------------


def test_known_beta_is_recovered():
    """The factor beta is recovered within tolerance, net of the market."""
    stock, market, factor = synthetic(beta=0.6)
    fit = prices.fit_sensitivity(stock, market, factor)
    assert fit is not None
    assert fit.beta == pytest.approx(0.6, abs=0.06)
    assert fit.t_stat > 10
    assert fit.n_obs == WEEKS
    assert fit.window_start == date(2023, 10, 20)
    assert fit.significant()


def test_negative_beta_is_recovered():
    """Sign is kept (an airline loses when oil rises)."""
    fit = prices.fit_sensitivity(*synthetic(beta=-0.4, seed=11))
    assert fit is not None and fit.beta == pytest.approx(-0.4, abs=0.06)


def test_market_control_removes_a_spurious_beta():
    """A stock driven only by the market looks exposed without the control, not with it."""
    stock, market, factor = synthetic(beta=0.0, seed=3)
    naive_slope = np.polyfit(factor.to_numpy(), stock.to_numpy(), 1)[0]
    fit = prices.fit_sensitivity(stock, market, factor)
    assert naive_slope > 0.1
    assert fit is not None and abs(fit.beta) < 0.06
    assert not fit.significant()


def test_partial_r2_matches_the_two_regressions():
    """partial r2 = t^2 / (t^2 + dof) = share of the market-only residual variance the factor explains."""
    stock, market, factor = synthetic(beta=0.3, seed=5)
    fit = prices.fit_sensitivity(stock, market, factor)

    def ssr(*columns):
        design = np.column_stack([np.ones(WEEKS), *columns])
        coefs, *_ = np.linalg.lstsq(design, stock.to_numpy(), rcond=None)
        residuals = stock.to_numpy() - design @ coefs
        return residuals @ residuals

    reduced, full = ssr(market.to_numpy()), ssr(market.to_numpy(), factor.to_numpy())
    assert fit is not None and fit.partial_r2 == pytest.approx((reduced - full) / reduced, rel=1e-9)


def test_missing_weeks_are_dropped_and_short_series_refused():
    """Weeks with a gap are dropped; under MIN_OBS common weeks there is no estimate."""
    stock, market, factor = synthetic(beta=0.6)
    stock.iloc[:10] = np.nan
    fit = prices.fit_sensitivity(stock, market, factor)
    assert fit is not None and fit.n_obs == WEEKS - 10
    assert prices.fit_sensitivity(*synthetic(beta=0.6, count=prices.MIN_OBS - 1)) is None


def test_constant_factor_gives_no_estimate():
    """A rate that never moves cannot be regressed on."""
    stock, market, _ = synthetic(beta=0.6)
    assert prices.fit_sensitivity(stock, market, pd.Series(0.0, index=stock.index)) is None


def test_write_rule_t_stat_or_r2():
    """Kept when |t| >= 2 or partial r2 >= 0.05."""
    base = {"beta": 0.1, "n_obs": 60, "window_start": date(2024, 1, 5), "window_end": date(2025, 1, 3)}
    assert prices.Fit(t_stat=-2.1, partial_r2=0.04, **base).significant()
    assert prices.Fit(t_stat=1.9, partial_r2=0.06, **base).significant()
    assert not prices.Fit(t_stat=1.5, partial_r2=0.04, **base).significant()


# --- Series preparation ------------------------------------------------------------------------------


def test_weekly_closes_returns_and_changes():
    """Daily closes become Friday closes; returns are in %, rate changes in percentage points."""
    days = pd.bdate_range("2026-01-05", periods=10)  # Mon 5 to Fri 16 January
    daily = pd.DataFrame({"STK": np.arange(100.0, 110.0), "RATE": np.linspace(3.0, 3.9, 10)}, index=days)
    weekly = prices.weekly_closes(daily)
    assert list(weekly.index) == [pd.Timestamp("2026-01-09"), pd.Timestamp("2026-01-16")]
    assert list(weekly["STK"]) == [104.0, 109.0]
    assert prices.weekly_returns(weekly)["STK"].iloc[1] == pytest.approx(500 / 104)
    assert prices.weekly_changes(weekly)["RATE"].iloc[1] == pytest.approx(0.5)


def test_unfinished_week_is_dropped():
    """On Monday 26 Oct the week labelled Friday 30 Oct is still running: it is not a weekly return."""
    frame = pd.DataFrame({"STK": [1.0, 2.0, 3.0]}, index=pd.to_datetime(["2026-10-16", "2026-10-23", "2026-10-30"]))
    kept = prices.complete_weeks(frame, start=date(2026, 10, 1), today=date(2026, 10, 26))
    assert list(kept.index) == [pd.Timestamp("2026-10-16"), pd.Timestamp("2026-10-23")]


def test_weekly_closes_drops_timezones():
    """Yahoo sometimes returns tz-aware timestamps; weeks must still align with other sources."""
    days = pd.date_range("2026-01-05 09:00", periods=5, freq="B", tz="Europe/Paris")
    weekly = prices.weekly_closes(pd.DataFrame({"STK": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=days))
    assert weekly.index.tz is None and list(weekly.index) == [pd.Timestamp("2026-01-09")]


def test_market_proxy_choice():
    """European listings are controlled by STOXX Europe 600, the rest by MSCI World."""
    assert prices.is_european("EUR", "TTE.PA")
    assert prices.is_european("GBp", "SHEL.L")
    assert not prices.is_european("USD", "AAPL")
    assert prices.is_european(None, "SAP.DE")
    assert not prices.is_european(None, "7203.T")
    assert not prices.is_european(None, "MSFT")


# --- Correlations ------------------------------------------------------------------------------------


def correlated_panel() -> pd.DataFrame:
    """A and B share a common driver (rho about 0.8); C and D are independent noise."""
    rng = np.random.default_rng(1)
    common = rng.normal(0, 1, WEEKS)
    return pd.DataFrame(
        {
            "B": common + rng.normal(0, 0.5, WEEKS),
            "A": common + rng.normal(0, 0.5, WEEKS),
            "C": rng.normal(0, 1, WEEKS),
            "D": rng.normal(0, 1, WEEKS),
        },
        index=weeks(),
    )


def test_top_correlations_stored_once_and_ordered():
    """The A-B pair appears once as (A, B); independent stocks have no edge."""
    pairs = prices.top_correlations(correlated_panel())
    assert [(pair["a"], pair["b"]) for pair in pairs] == [("A", "B")]
    assert 0.7 < pairs[0]["rho"] < 0.9


def test_top_correlations_respects_threshold_and_k():
    """No pair below min_rho, and each stock contributes at most top_k neighbours of its own."""
    rng = np.random.default_rng(2)
    common = rng.normal(0, 1, WEEKS)
    panel = pd.DataFrame({f"S{i}": common + rng.normal(0, 0.6, WEEKS) for i in range(6)}, index=weeks())
    pairs = prices.top_correlations(panel, top_k=2, min_rho=0.5)
    assert all(pair["rho"] >= 0.5 and pair["a"] < pair["b"] for pair in pairs)
    assert 3 <= len(pairs) <= 6 * 2
    assert prices.top_correlations(panel, top_k=2, min_rho=0.99) == []


def test_market_residual_is_orthogonal_to_the_market():
    """What is left after removing the market does not move with the market."""
    stock, market, _ = synthetic(beta=0.6)
    residual = prices.market_residual(stock, market)
    assert abs(np.corrcoef(residual, market)[0, 1]) < 1e-9
    assert prices.market_residual(stock.iloc[:10], market.iloc[:10]).isna().all()


# --- Downloads and cache (network replaced by fakes) -------------------------------------------------


class FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, text: str = "", payload: dict | None = None) -> None:
        self.text = text
        self.payload = payload

    def raise_for_status(self) -> None:
        """Always OK."""

    def json(self) -> dict:
        """Return the JSON payload."""
        return self.payload or {}


def test_fetch_fred_json_and_csv(monkeypatch):
    """With a key the JSON API is used, else the CSV export; '.' marks a missing day."""
    payload = {"observations": [{"date": "2026-01-05", "value": "4.10"}, {"date": "2026-01-06", "value": "."}]}
    monkeypatch.setattr(prices.httpx, "get", lambda url, **kwargs: FakeResponse(payload=payload))
    series = prices.fetch_fred("DGS10", date(2026, 1, 1), api_key="key")
    assert list(series) == [4.10]

    csv = "observation_date,DGS10\n2026-01-05,4.10\n2026-01-06,4.20\n"
    monkeypatch.setattr(prices.httpx, "get", lambda url, **kwargs: FakeResponse(text=csv))
    assert list(prices.fetch_fred("DGS10", date(2026, 1, 1))) == [4.10, 4.20]


def test_fetch_ecb(monkeypatch):
    """The ECB csvdata format has TIME_PERIOD and OBS_VALUE columns."""
    csv = "KEY,TIME_PERIOD,OBS_VALUE\nYC.X,2026-01-06,2.95\nYC.X,2026-01-05,2.90\n"
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(text=csv)

    monkeypatch.setattr(prices.httpx, "get", fake_get)
    series = prices.fetch_ecb("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y", date(2026, 1, 1))
    assert list(series) == [2.90, 2.95]
    assert calls[0].endswith("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y")


def test_fetch_yahoo_chunks_and_single_ticker(monkeypatch):
    """Symbols are downloaded in chunks; a single-ticker answer (Series) becomes a column."""
    index = pd.bdate_range("2026-01-05", periods=3)

    def fake_download(chunk, **kwargs):
        del kwargs
        if len(chunk) == 1:
            return pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=index)
        return pd.concat({"Close": pd.DataFrame({name: [1.0, 2.0, 3.0] for name in chunk}, index=index)}, axis=1)

    sleeps: list[float] = []
    monkeypatch.setattr(prices, "YAHOO_CHUNK", 2)
    monkeypatch.setattr(prices.yf, "download", fake_download)
    monkeypatch.setattr(prices.time, "sleep", sleeps.append)
    frame = prices.fetch_yahoo(["AAA", "BBB", "CCC"], date(2026, 1, 1))
    assert list(frame.columns) == ["AAA", "BBB", "CCC"]
    assert sleeps == [prices.YAHOO_PAUSE]  # between the two chunks only


def test_fetch_yahoo_retries_failed_symbols_after_a_wait(monkeypatch):
    """A symbol lost by the threaded call (refused, or 'database is locked') is fetched again sequentially,
    after a wait so that a rate limit has time to clear."""
    index = pd.bdate_range("2026-01-05", periods=3)
    calls, sleeps = [], []

    def fake_download(chunk, threads, **kwargs):
        del kwargs
        calls.append((list(chunk), threads))
        kept = [name for name in chunk if not threads or name != "TTE.PA"]
        return pd.concat({"Close": pd.DataFrame({name: [1.0, 2.0, 3.0] for name in kept}, index=index)}, axis=1)

    monkeypatch.setattr(prices.yf, "download", fake_download)
    monkeypatch.setattr(prices.time, "sleep", sleeps.append)
    frame = prices.fetch_yahoo(["AF.PA", "TTE.PA"], date(2026, 1, 1))
    assert sorted(frame.columns) == ["AF.PA", "TTE.PA"]
    assert calls == [(["AF.PA", "TTE.PA"], prices.YAHOO_THREADS), (["TTE.PA"], False)]
    assert sleeps == [prices.YAHOO_RETRY_WAIT]


def test_cache_downloads_only_missing_symbols(tmp_path):
    """Second call with one new symbol only downloads that symbol. A symbol that came back empty is not
    cached: it is asked again (an empty answer can be a refusal, not only an unknown symbol)."""
    index = pd.bdate_range("2026-01-05", periods=3)
    asked: list[list[str]] = []

    def fetch(symbols):
        asked.append(symbols)
        return pd.DataFrame({name: [1.0, 2.0, 3.0] for name in symbols if name != "UNKNOWN"}, index=index)

    path = tmp_path / "cache" / "yahoo_daily.parquet"
    prices.cached_frame(path, ["AAA", "UNKNOWN"], fetch)
    frame = prices.cached_frame(path, ["AAA", "BBB", "UNKNOWN"], fetch)
    assert asked == [["AAA", "UNKNOWN"], ["BBB", "UNKNOWN"]]
    assert list(frame.columns) == ["AAA", "BBB", "UNKNOWN"] and frame["UNKNOWN"].isna().all()
    assert "UNKNOWN" not in pd.read_parquet(path).columns


def test_cache_written_by_the_old_rule_asks_its_empty_columns_again(tmp_path):
    """A cache file that still holds empty columns (written before this rule) gets them downloaded again."""
    index = pd.bdate_range("2026-01-05", periods=3)
    path = tmp_path / "yahoo_daily.parquet"
    pd.DataFrame({"AAA": [1.0, 2.0, 3.0], "LOST": [None] * 3}, index=index).to_parquet(path)
    asked: list[list[str]] = []

    def fetch(symbols):
        asked.append(symbols)
        return pd.DataFrame({name: [4.0, 5.0, 6.0] for name in symbols}, index=index)

    frame = prices.cached_frame(path, ["AAA", "LOST"], fetch)
    assert asked == [["LOST"]]
    assert frame["LOST"].tolist() == [4.0, 5.0, 6.0]


# --- Pipeline with a fake database -------------------------------------------------------------------


class ScriptedClient:
    """Answers the universe queries and records the writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    def read(self, query: str, params: dict | None = None) -> list[dict]:
        """Companies or factors depending on the query."""
        del params
        if "MATCH (c:Company)" in query:
            return [
                {"isin": ZZ_ISINS[0], "ticker": "ZZA.PA", "currency": "EUR"},
                {"isin": ZZ_ISINS[1], "ticker": "ZZB.PA", "currency": "EUR"},
                {"isin": ZZ_ISINS[2], "ticker": "ZZC", "currency": "USD"},
            ]
        return [
            {"code": "BRENT", "kind": "commodity", "source": "yahoo", "source_id": "BZ=F"},
            {"code": "EUR10Y", "kind": "rate", "source": "ecb", "source_id": "YC/SERIES"},
            {"code": "US10Y", "kind": "rate", "source": "fred", "source_id": "DGS10"},
        ]

    def write(self, query: str, params: dict | None = None) -> list[dict]:
        """Record the write and pretend every row was written."""
        self.writes.append((query, params or {}))
        return [{"written": len((params or {}).get("rows", []))}]


def test_run_end_to_end_without_network(monkeypatch, tmp_path):
    """Synthetic prices go through cache, regressions and writes; BRENT and the rate betas are found."""
    rng = np.random.default_rng(4)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=3 * 261 + 10)
    count = len(days)
    market = np.cumsum(rng.normal(0, 0.01, count))
    oil = np.cumsum(rng.normal(0, 0.04, count))
    rate = 3 + np.cumsum(rng.normal(0, 0.03, count))
    common = np.cumsum(rng.normal(0, 0.02, count))  # shared by A and B only: their residuals correlate
    logs = {
        "EXSA.DE": market,
        "URTH": market,
        "BZ=F": oil,
        "ZZA.PA": market + 0.6 * oil + common + np.cumsum(rng.normal(0, 0.004, count)),
        "ZZB.PA": market + 0.4 * oil + common + np.cumsum(rng.normal(0, 0.004, count)),
        "ZZC": market + 0.05 * (rate - 3) + np.cumsum(rng.normal(0, 0.004, count)),
    }
    closes = pd.DataFrame({name: 100 * np.exp(values) for name, values in logs.items()}, index=days)
    monkeypatch.setattr(prices, "fetch_yahoo", lambda symbols, start: closes[[s for s in symbols if s in closes]])
    monkeypatch.setattr(prices, "fetch_ecb", lambda key, start: pd.Series(rate, index=days))
    monkeypatch.setattr(prices, "fetch_fred", lambda series_id, start, api_key="": pd.Series(rate, index=days))

    client = ScriptedClient()
    report = prices.run(client, Settings(data_dir=str(tmp_path)))

    assert report["companies"] == 3 and report["with_prices"] == 3
    assert report["factors"] == ["BRENT", "EUR10Y", "US10Y"]
    assert (tmp_path / "cache" / "yahoo_daily.parquet").exists()
    rows = [row for query, params in client.writes if "SENSITIVE_TO {" in query for row in params["rows"]]
    by_key = {(row["isin"], row["factor"]): row for row in rows}
    assert by_key[(ZZ_ISINS[0], "BRENT")]["beta"] == pytest.approx(0.6, abs=0.1)
    assert by_key[(ZZ_ISINS[1], "BRENT")]["beta"] == pytest.approx(0.4, abs=0.1)
    assert by_key[(ZZ_ISINS[2], "US10Y")]["move_unit"] == "pp"
    assert by_key[(ZZ_ISINS[2], "US10Y")]["beta"] == pytest.approx(5.0, abs=1.0)  # 5 % per +1 pp
    pairs = [row for query, params in client.writes if "CORRELATED_WITH {" in query for row in params["rows"]]
    assert [(pair["a"], pair["b"]) for pair in pairs] == [(ZZ_ISINS[0], ZZ_ISINS[1])]


# --- Writes on the test database ---------------------------------------------------------------------


@pytest.fixture
def zz_companies(neo4j_client):
    """Three throw-away companies (ZZ ISINs), deleted with their relationships afterwards."""
    neo4j_client.write("UNWIND $isins AS isin CREATE (:Company {isin: isin, name: 'C1 test'})", {"isins": ZZ_ISINS})
    yield neo4j_client
    neo4j_client.write("MATCH (c:Company) WHERE c.isin IN $isins DETACH DELETE c", {"isins": ZZ_ISINS})


def estimate_row(isin: str, factor: str, beta: float) -> dict:
    """A SENSITIVE_TO row as produced by estimate_sensitivities."""
    return {
        "isin": isin,
        "factor": factor,
        "beta": beta,
        "r2": 0.2,
        "t_stat": 5.0,
        "n_obs": 156,
        "window_start": "2023-10-20",
        "window_end": "2026-10-16",
        "method": prices.METHOD,
        "move_unit": "pct",
    }


@pytest.mark.neo4j
def test_writes_replace_estimates_and_leave_the_mini_graph_alone(zz_companies):
    """Writing twice replaces (no duplicates); fixture estimates are untouched."""
    client = zz_companies
    count_query = "MATCH (:Company)-[r:SENSITIVE_TO]->(:Factor) RETURN count(r) AS n"
    before = client.read(count_query)[0]["n"]

    assert prices.write_sensitivities(client, ZZ_ISINS, [estimate_row(ZZ_ISINS[0], "BRENT", 0.5)]) == 1
    rows = [estimate_row(ZZ_ISINS[0], "BRENT", 0.7), estimate_row(ZZ_ISINS[1], "COPPER", -0.3)]
    assert prices.write_sensitivities(client, ZZ_ISINS, rows) == 2
    betas = client.read(
        "MATCH (c:Company)-[r:SENSITIVE_TO]->(x:Factor) WHERE c.isin IN $isins "
        "RETURN c.isin AS isin, x.code AS factor, r.beta AS beta, r.window_end AS window_end ORDER BY isin",
        {"isins": ZZ_ISINS},
    )
    assert [(row["isin"], row["factor"], row["beta"]) for row in betas] == [
        (ZZ_ISINS[0], "BRENT", 0.7),
        (ZZ_ISINS[1], "COPPER", -0.3),
    ]
    assert str(betas[0]["window_end"]) == "2026-10-16"
    assert client.read(count_query)[0]["n"] == before + 2

    pairs = [{"a": ZZ_ISINS[0], "b": ZZ_ISINS[1], "rho": 0.6}]
    assert prices.write_correlations(client, ZZ_ISINS, pairs, date(2026, 10, 16)) == 1
    assert prices.write_correlations(client, ZZ_ISINS, pairs, date(2026, 10, 16)) == 1
    stored = client.read(
        "MATCH (a:Company)-[r:CORRELATED_WITH]->(b:Company) WHERE a.isin IN $isins RETURN a.isin AS a, b.isin AS b",
        {"isins": ZZ_ISINS},
    )
    assert stored == [{"a": ZZ_ISINS[0], "b": ZZ_ISINS[1]}]
    assert client.read("MATCH ()-[r:CORRELATED_WITH]->() RETURN count(r) AS n")[0]["n"] == 6 + 1


def test_listings_and_factors_from_rows(fake_client):
    """Market proxy chosen from currency; rates move in percentage points."""
    fake_client.rows = [{"isin": "ZZ1", "ticker": "ZZA.PA", "currency": "EUR"}]
    assert prices.load_listings(fake_client) == [Listing("ZZ1", "ZZA.PA", prices.MARKET_EUROPE)]
    fake_client.rows = [{"code": "EUR10Y", "kind": "rate", "source": "ecb", "source_id": "YC/X"}]
    assert prices.load_factors(fake_client) == [FactorSource("EUR10Y", "ecb", "YC/X", "pp")]
    assert all(params["limit"] == prices.MAX_ROWS for _, params in fake_client.queries)
