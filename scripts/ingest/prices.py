"""Step 3 (role C, C1): prices, sensitivities and correlations, truth level `estimate`.

Sources: Yahoo Finance (stocks, commodity futures, FX, market proxies), FRED and ECB Data Portal (rates).
Daily closes are cached in `data/cache/*.parquet`, then resampled to weekly (Friday) closes over
`returns_window_years`.

Units: every move is in percent (stocks, market, commodities, FX), except rates which move in
percentage points. So `beta` reads "stock move in % for a 1 % factor move" (or a +1 pp rate move) and
the shock service can use `impact_pct = beta * pct` directly. `move_unit` on the relationship says which.

SENSITIVE_TO: OLS `r_stock = a + b_m * r_market + b_f * r_factor`, one regression per (stock, factor).
The market control removes the move every stock shares; `beta = b_f`, with its t-stat and partial r2
(share of the variance left by the market that the factor explains). Written only when |t| >= 2 or
partial r2 >= 0.05.
CORRELATED_WITH: correlation of the market residuals, top 5 neighbours per stock with rho >= 0.5,
stored once (`a.isin < b.isin`).
"""

import io
import math
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import yfinance as yf

from app.config import Settings
from app.database.neo4j import Neo4jClient

METHOD = "ols_market_controlled"
MARKET_EUROPE = "EXSA.DE"  # iShares STOXX Europe 600 UCITS ETF (DE), EUR, Xetra
MARKET_WORLD = "URTH"  # iShares MSCI World ETF, USD
EUROPE_CURRENCIES = {"EUR", "GBP", "GBp", "GBX", "CHF", "SEK", "DKK", "NOK", "PLN", "CZK", "HUF"}
EUROPE_SUFFIXES = {
    "PA", "DE", "F", "AS", "BR", "MI", "MC", "LS", "HE", "IR", "VI", "ST", "CO", "OL", "SW", "L", "WA", "PR",
}  # fmt: skip

MIN_OBS = 52  # one year of weekly returns
MIN_T_STAT = 2.0
MIN_PARTIAL_R2 = 0.05
TOP_NEIGHBOURS = 5
MIN_RHO = 0.5
MAX_ROWS = 20_000  # LIMIT on the universe queries

YAHOO_CHUNK = 100
CACHE_MAX_AGE_DAYS = 7
WRITE_BATCH = 1_000
HTTP_TIMEOUT = 60
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/"


@dataclass(frozen=True)
class Listing:
    """A company with a Yahoo ticker and the market proxy used to control its regressions."""

    isin: str
    ticker: str
    market: str


@dataclass(frozen=True)
class FactorSource:
    """Where a factor's series comes from and the unit its moves are measured in."""

    code: str
    source: str
    source_id: str
    move_unit: str  # "pct" (returns) or "pp" (changes in a rate)


@dataclass(frozen=True)
class Fit:
    """Result of one market-controlled regression."""

    beta: float
    t_stat: float
    partial_r2: float
    n_obs: int
    window_start: date
    window_end: date

    def significant(self) -> bool:
        """Write rule of C1: |t| >= 2 or partial r2 >= 0.05."""
        return abs(self.t_stat) >= MIN_T_STAT or self.partial_r2 >= MIN_PARTIAL_R2


# --- Pure computations (unit tested on synthetic series, no network) ---------------------------------


def is_european(currency: str | None, ticker: str) -> bool:
    """True when the stock trades in Europe: by currency, else by the Yahoo suffix (`TTE.PA`)."""
    if currency:
        return currency in EUROPE_CURRENCIES
    return "." in ticker and ticker.rsplit(".", 1)[1].upper() in EUROPE_SUFFIXES


def weekly_closes(daily: pd.DataFrame) -> pd.DataFrame:
    """Last close of each week, labelled by its Friday. A week without any trade stays empty."""
    frame = daily.sort_index()
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    frame.index = index.normalize()
    return frame.resample("W-FRI").last()


def weekly_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """Weekly returns in percent. No forward fill: a missing week gives two missing returns."""
    return (closes / closes.shift(1) - 1) * 100


def weekly_changes(levels: pd.DataFrame) -> pd.DataFrame:
    """Weekly changes in percentage points (for rates: a return on a yield has no meaning)."""
    return levels.diff()


def fit_sensitivity(stock: pd.Series, market: pd.Series, factor: pd.Series) -> Fit | None:
    """OLS of the stock on the market and the factor over their common weeks; None when unusable."""
    frame = pd.concat({"stock": stock, "market": market, "factor": factor}, axis=1).dropna()
    n_obs = len(frame)
    if n_obs < MIN_OBS or frame["factor"].std() == 0 or frame["market"].std() == 0:
        return None
    design = np.column_stack([np.ones(n_obs), frame["market"].to_numpy(), frame["factor"].to_numpy()])
    target = frame["stock"].to_numpy()
    try:
        inverse = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:  # market and factor perfectly collinear
        return None
    coefs = inverse @ design.T @ target
    residuals = target - design @ coefs
    dof = n_obs - design.shape[1]
    variance = float(residuals @ residuals) / dof
    std_error = math.sqrt(variance * inverse[2, 2])
    if std_error == 0:
        return None
    t_stat = float(coefs[2]) / std_error
    return Fit(
        beta=float(coefs[2]),
        t_stat=t_stat,
        partial_r2=t_stat**2 / (t_stat**2 + dof),
        n_obs=n_obs,
        window_start=frame.index[0].date(),
        window_end=frame.index[-1].date(),
    )


def market_residual(stock: pd.Series, market: pd.Series) -> pd.Series:
    """Part of the stock's weekly move not explained by the market (OLS a + b * market)."""
    frame = pd.concat({"stock": stock, "market": market}, axis=1).dropna()
    if len(frame) < MIN_OBS or frame["market"].std() == 0:
        return pd.Series(dtype=float, index=stock.index)
    slope, intercept = np.polyfit(frame["market"].to_numpy(), frame["stock"].to_numpy(), 1)
    return (frame["stock"] - intercept - slope * frame["market"]).reindex(stock.index)


def top_correlations(residuals: pd.DataFrame, top_k: int = TOP_NEIGHBOURS, min_rho: float = MIN_RHO) -> list[dict]:
    """Keep each stock's `top_k` most correlated neighbours with rho >= `min_rho`.

    A pair kept by either side is stored once, ordered so that `a < b`. Columns are ISINs.
    """
    corr = residuals.corr(min_periods=MIN_OBS)
    values = corr.to_numpy(copy=True)
    np.fill_diagonal(values, np.nan)
    names = list(corr.columns)
    pairs: dict[tuple[str, str], float] = {}
    for row, name in enumerate(names):
        line = values[row]
        for col in np.argsort(-np.nan_to_num(line, nan=-np.inf))[:top_k]:
            rho = line[col]
            if np.isnan(rho) or rho < min_rho:
                break
            first, second = sorted((name, names[col]))
            pairs[(first, second)] = float(rho)
    return [{"a": first, "b": second, "rho": round(rho, 4)} for (first, second), rho in sorted(pairs.items())]


def estimate_sensitivities(
    listings: list[Listing], prices: pd.DataFrame, factors: list[FactorSource], factor_moves: pd.DataFrame
) -> tuple[list[dict], int]:
    """Run every (stock, factor) regression; return the rows to write and the number of fits tried."""
    rows: list[dict] = []
    tried = 0
    for listing in listings:
        if listing.ticker not in prices or listing.market not in prices:
            continue
        for factor in factors:
            if factor.code not in factor_moves:
                continue
            tried += 1
            fit = fit_sensitivity(prices[listing.ticker], prices[listing.market], factor_moves[factor.code])
            if fit is None or not fit.significant():
                continue
            rows.append(
                {
                    "isin": listing.isin,
                    "factor": factor.code,
                    "beta": round(fit.beta, 4),
                    "r2": round(fit.partial_r2, 4),
                    "t_stat": round(fit.t_stat, 2),
                    "n_obs": fit.n_obs,
                    "window_start": fit.window_start.isoformat(),
                    "window_end": fit.window_end.isoformat(),
                    "method": METHOD,
                    "move_unit": factor.move_unit,
                }
            )
    return rows, tried


def residuals_by_isin(listings: list[Listing], prices: pd.DataFrame) -> pd.DataFrame:
    """Market residuals of every listed stock, one column per ISIN."""
    columns = {
        listing.isin: market_residual(prices[listing.ticker], prices[listing.market])
        for listing in listings
        if listing.ticker in prices and listing.market in prices
    }
    return pd.DataFrame(columns)


# --- Downloads and cache ------------------------------------------------------------------------------


def _download_closes(symbols: list[str], start: date, threads: bool) -> pd.DataFrame:
    """One yfinance call; symbols Yahoo returned nothing for are absent from the result."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # yfinance still calls pandas APIs deprecated in pandas 3
        data = yf.download(
            symbols, start=start.isoformat(), interval="1d", auto_adjust=True, progress=False, threads=threads
        )
    if data is None or data.empty:
        return pd.DataFrame()
    closes = data["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(symbols[0])
    return closes.dropna(axis=1, how="all")


def fetch_yahoo(symbols: list[str], start: date) -> pd.DataFrame:
    """Daily adjusted closes from Yahoo Finance, one column per symbol (unknown symbols are dropped).

    Threaded downloads sometimes fail on yfinance's own local cache ("database is locked"), so the
    symbols missing from a chunk are retried once without threads.
    """
    frames = []
    for offset in range(0, len(symbols), YAHOO_CHUNK):
        chunk = symbols[offset : offset + YAHOO_CHUNK]
        closes = _download_closes(chunk, start, threads=True)
        failed = [symbol for symbol in chunk if symbol not in closes.columns]
        if failed:
            retry = _download_closes(failed, start, threads=False)
            closes = retry if closes.columns.empty else pd.concat([closes, retry], axis=1)
        frames.append(closes)
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def fetch_fred(series_id: str, start: date, api_key: str = "") -> pd.Series:
    """Daily FRED series (the JSON API with a key, else the public CSV export). '.' means missing."""
    if api_key:
        params = {"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start}
        response = httpx.get(FRED_URL, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        observations = response.json()["observations"]
        series = pd.Series({row["date"]: row["value"] for row in observations}, dtype=object)
    else:
        params = {"id": series_id, "cosd": start.isoformat()}
        response = httpx.get(FRED_CSV_URL, params=params, timeout=HTTP_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
        series = frame.set_index(frame.columns[0])[frame.columns[1]]
    return _numeric_series(series)


def fetch_ecb(series_key: str, start: date) -> pd.Series:
    """Daily ECB Data Portal series, e.g. `YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y`."""
    params = {"format": "csvdata", "startPeriod": start.isoformat()}
    response = httpx.get(ECB_URL + series_key, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    return _numeric_series(frame.set_index("TIME_PERIOD")["OBS_VALUE"])


def _numeric_series(series: pd.Series) -> pd.Series:
    """Dates as index, floats as values, missing observations dropped."""
    values = pd.to_numeric(series, errors="coerce")
    values.index = pd.to_datetime(values.index)
    return values.dropna().sort_index()


def _is_fresh(path: Path) -> bool:
    """True when the cache file exists and is younger than CACHE_MAX_AGE_DAYS."""
    return path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_DAYS * 86_400


def cached_frame(path: Path, symbols: list[str], fetch: Callable[[list[str]], pd.DataFrame]) -> pd.DataFrame:
    """Read the parquet cache, download only the symbols it lacks (all of them when stale), save it back."""
    frame = pd.read_parquet(path) if _is_fresh(path) else pd.DataFrame()
    missing = [symbol for symbol in symbols if symbol not in frame.columns]
    if missing:
        fresh = fetch(missing)
        # A symbol the source does not know is cached as an empty column so it is not asked again.
        fresh = fresh.reindex(columns=missing)
        frame = fresh if frame.columns.empty else pd.concat([frame, fresh], axis=1).sort_index()
        frame.columns = [str(column) for column in frame.columns]
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path)
    return frame.reindex(columns=symbols)


def load_rates(factors: list[FactorSource], start: date, settings: Settings, cache_dir: Path) -> pd.DataFrame:
    """Daily levels of the FRED and ECB rate factors, one column per factor code."""
    by_code = {factor.code: factor for factor in factors if factor.source in ("fred", "ecb")}

    def fetch(codes: list[str]) -> pd.DataFrame:
        series = {}
        for code in codes:
            factor = by_code[code]
            if factor.source == "fred":
                series[code] = fetch_fred(factor.source_id, start, settings.fred_api_key)
            else:
                series[code] = fetch_ecb(factor.source_id, start)
        return pd.DataFrame(series)

    return cached_frame(cache_dir / "rates_daily.parquet", sorted(by_code), fetch)


# --- Neo4j --------------------------------------------------------------------------------------------


def load_listings(client: Neo4jClient) -> list[Listing]:
    """Companies with a Yahoo ticker, each with its market proxy."""
    rows = client.read(
        "MATCH (c:Company) WHERE c.ticker IS NOT NULL "
        "RETURN c.isin AS isin, c.ticker AS ticker, c.currency AS currency ORDER BY isin LIMIT $limit",
        {"limit": MAX_ROWS},
    )
    return [
        Listing(
            row["isin"], row["ticker"], MARKET_EUROPE if is_european(row["currency"], row["ticker"]) else MARKET_WORLD
        )
        for row in rows
    ]


def load_factors(client: Neo4jClient) -> list[FactorSource]:
    """Factors with a known source; rates move in percentage points, everything else in percent."""
    rows = client.read(
        "MATCH (x:Factor) WHERE x.source IS NOT NULL AND x.source_id IS NOT NULL "
        "RETURN x.code AS code, x.kind AS kind, x.source AS source, x.source_id AS source_id "
        "ORDER BY code LIMIT $limit",
        {"limit": MAX_ROWS},
    )
    return [
        FactorSource(row["code"], row["source"], row["source_id"], "pp" if row["kind"] == "rate" else "pct")
        for row in rows
    ]


def _batches(rows: list[dict]) -> list[list[dict]]:
    """Split rows in chunks of WRITE_BATCH for UNWIND."""
    return [rows[offset : offset + WRITE_BATCH] for offset in range(0, len(rows), WRITE_BATCH)]


def write_sensitivities(client: Neo4jClient, isins: list[str], rows: list[dict]) -> int:
    """Replace the SENSITIVE_TO estimates of `isins` (this method only) by `rows`; return how many."""
    client.write(
        "MATCH (c:Company)-[r:SENSITIVE_TO]->(:Factor) WHERE c.isin IN $isins AND r.method = $method DELETE r",
        {"isins": isins, "method": METHOD},
    )
    written = 0
    for batch in _batches(rows):
        result = client.write(
            "UNWIND $rows AS row "
            "MATCH (c:Company {isin: row.isin}) MATCH (x:Factor {code: row.factor}) "
            "CREATE (c)-[:SENSITIVE_TO {beta: row.beta, r2: row.r2, t_stat: row.t_stat, n_obs: row.n_obs, "
            "window_start: date(row.window_start), window_end: date(row.window_end), method: row.method, "
            "move_unit: row.move_unit}]->(x) "
            "RETURN count(*) AS written",
            {"rows": batch},
        )
        written += result[0]["written"] if result else 0
    return written


def write_correlations(client: Neo4jClient, isins: list[str], pairs: list[dict], window_end: date) -> int:
    """Replace the CORRELATED_WITH estimates touching `isins` by `pairs` (`a < b`); return how many."""
    client.write(
        "MATCH (a:Company)-[r:CORRELATED_WITH]->(b:Company) WHERE a.isin IN $isins OR b.isin IN $isins DELETE r",
        {"isins": isins},
    )
    written = 0
    for batch in _batches(pairs):
        result = client.write(
            "UNWIND $rows AS row "
            "MATCH (a:Company {isin: row.a}) MATCH (b:Company {isin: row.b}) "
            "CREATE (a)-[:CORRELATED_WITH {rho: row.rho, window_end: date($window_end)}]->(b) "
            "RETURN count(*) AS written",
            {"rows": batch, "window_end": window_end.isoformat()},
        )
        written += result[0]["written"] if result else 0
    return written


# --- Pipeline -----------------------------------------------------------------------------------------


def weekly_moves(
    listings: list[Listing],
    factors: list[FactorSource],
    settings: Settings,
    start: date,
    cache_dir: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Weekly moves of stocks and market proxies (by ticker) and of factors (by code), from `start`.

    The cache keeps the history it was first filled with, so a caller that needs a longer history (the ML
    model's two windows) passes its own `cache_dir`.
    """
    cache_dir = cache_dir or Path(settings.data_dir) / "cache"
    yahoo_factors = {factor.source_id: factor.code for factor in factors if factor.source == "yahoo"}
    symbols = sorted({listing.ticker for listing in listings} | {MARKET_EUROPE, MARKET_WORLD} | set(yahoo_factors))
    yahoo = weekly_returns(
        weekly_closes(cached_frame(cache_dir / "yahoo_daily.parquet", symbols, _fetch_yahoo_from(start)))
    )
    rates = load_rates(factors, start, settings, cache_dir)
    moves = yahoo[list(yahoo_factors)].rename(columns=yahoo_factors)
    if not rates.empty:
        moves = moves.join(weekly_changes(weekly_closes(rates)), how="outer")
    return complete_weeks(yahoo, start), complete_weeks(moves, start)


def complete_weeks(frame: pd.DataFrame, start: date, today: date | None = None) -> pd.DataFrame:
    """Drop the first week (no previous close) and the current week, which has not ended yet."""
    first_week = pd.Timestamp(start) + pd.Timedelta(days=7)
    last_week = pd.Timestamp(today or date.today())
    return frame[(frame.index >= first_week) & (frame.index <= last_week)]


def _fetch_yahoo_from(start: date) -> Callable[[list[str]], pd.DataFrame]:
    """Bind the start date so the cache can call the downloader with the symbols only."""
    return lambda symbols: fetch_yahoo(symbols, start)


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Download, compute and write the estimates; return counts."""
    listings = load_listings(client)
    factors = load_factors(client)
    start = date.today() - timedelta(days=365 * settings.returns_window_years + 7)
    prices, factor_moves = weekly_moves(listings, factors, settings, start)

    sensitivities, tried = estimate_sensitivities(listings, prices, factors, factor_moves)
    correlations = top_correlations(residuals_by_isin(listings, prices))
    priced = [listing.isin for listing in listings if prices.get(listing.ticker, pd.Series()).notna().sum() >= MIN_OBS]
    window_end = prices.index[-1].date() if len(prices.index) else date.today()
    return {
        "companies": len(listings),
        "with_prices": len(priced),
        "factors": sorted(factor_moves.columns),
        "fits_tried": tried,
        "sensitive_to": write_sensitivities(client, priced, sensitivities),
        "correlated_with": write_correlations(client, priced, correlations, window_end),
    }
