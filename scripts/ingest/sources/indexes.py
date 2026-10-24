"""Index member lists not published as SSGA holdings, and the EUR/USD rate.

- Nasdaq-100: official Nasdaq API (symbols with market caps in USD).
- CAC 40: Wikipedia constituents table (Yahoo symbols).
- EUR/USD: ECB Data Portal, latest daily reference rate.
"""

import io

import httpx
import pandas as pd

USER_AGENT = "shockgraph-pea student project (github.com/Polluxgnr/shockgraph-pea)"
NASDAQ_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
CAC40_URL = "https://en.wikipedia.org/wiki/CAC_40"
ECB_EURUSD_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"


def parse_market_cap(text: str) -> float | None:
    """'5,074,761,240,500' -> 5074761240500.0; empty or 'NA' -> None."""
    cleaned = str(text).replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def nasdaq100() -> list[dict]:
    """Members of the Nasdaq-100: [{'symbol', 'name', 'market_cap_usd'}]."""
    response = httpx.get(NASDAQ_URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=30)
    response.raise_for_status()
    rows = response.json()["data"]["data"]["rows"]
    return [
        {"symbol": row["symbol"], "name": row["companyName"], "market_cap_usd": parse_market_cap(row["marketCap"])}
        for row in rows
    ]


def cac40() -> list[str]:
    """Yahoo symbols of the CAC 40 members (e.g. 'AI.PA', 'MT.AS')."""
    response = httpx.get(CAC40_URL, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)
    response.raise_for_status()
    for table in pd.read_html(io.StringIO(response.text), flavor="lxml"):
        if "Ticker" in [str(column) for column in table.columns] and len(table) >= 38:
            return [str(symbol).strip() for symbol in table["Ticker"]]
    raise ValueError("CAC 40 table not found on Wikipedia")


def eur_usd() -> float:
    """Latest ECB reference rate: how many USD for one EUR."""
    response = httpx.get(ECB_EURUSD_URL, params={"lastNObservations": 1, "format": "jsondata"}, timeout=30)
    response.raise_for_status()
    series = next(iter(response.json()["dataSets"][0]["series"].values()))
    return float(next(iter(series["observations"].values()))[0])
