"""SSGA (State Street SPDR) daily ETF holdings: our proxy for index constituents and weights.

Each SPDR UCITS ETF publishes an xlsx with ISIN, name, weight, GICS sector and industry, listing country.
A fund that fully replicates an index holds (almost) exactly its constituents with the same weights.
"""

import re
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd

URL = "https://www.ssga.com/library-content/products/fund-data/etfs/emea/holdings-daily-emea-en-{key}.xlsx"
ISIN_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
COLUMNS = {
    "ISIN": "isin",
    "Security Name": "name",
    "Currency": "currency",
    "Percent of Fund": "weight",
    "Trade Country Name": "listing_country",
    "Sector Classification": "sector",
    "Industry Classification": "industry",
    "Base Market Value": "market_value",
}


def download(key: str, raw_dir: Path, refresh: bool = False) -> Path:
    """Download the holdings file of one ETF (e.g. 'spyy-gy') into `raw_dir`, unless already there."""
    path = raw_dir / f"{key}.xlsx"
    if path.exists() and not refresh:
        return path
    raw_dir.mkdir(parents=True, exist_ok=True)
    response = httpx.get(URL.format(key=key), headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
    response.raise_for_status()
    if "spreadsheet" not in response.headers.get("content-type", ""):
        raise ValueError(f"SSGA did not return a spreadsheet for {key}")
    path.write_bytes(response.content)
    return path


def parse_holdings(path: Path) -> tuple[pd.DataFrame, date | None]:
    """Parse a holdings file into one row per equity line with `weight` in [0, 1] summing to 1.

    Rows without a valid ISIN (cash, futures, footers) are dropped; duplicate ISINs (share classes)
    are merged by summing their weights.
    """
    raw = pd.read_excel(path, header=None)
    first_column = raw.iloc[:, 0].astype(str).str.strip()
    header_row = int(raw.index[first_column.eq("ISIN")][0])
    as_of = _as_of(raw.iloc[:header_row, :2])
    frame = pd.read_excel(path, header=header_row)
    frame = frame.rename(columns=COLUMNS)[list(COLUMNS.values())]
    frame = frame[frame["isin"].astype(str).str.match(ISIN_REGEX)].copy()
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0)
    frame["market_value"] = pd.to_numeric(frame["market_value"], errors="coerce").fillna(0.0)
    frame = frame[frame["weight"] > 0]
    aggregated = frame.groupby("isin", as_index=False).agg(
        {
            "name": "first",
            "currency": "first",
            "weight": "sum",
            "listing_country": "first",
            "sector": "first",
            "industry": "first",
            "market_value": "sum",
        }
    )
    aggregated["weight"] = aggregated["weight"] / aggregated["weight"].sum()
    return aggregated.sort_values("weight", ascending=False, ignore_index=True), as_of


def _as_of(meta: pd.DataFrame) -> date | None:
    """Read the 'Holdings As Of' date from the header block of the file."""
    for label, value in meta.itertuples(index=False):
        if str(label).strip().lower().startswith("holdings as of"):
            if isinstance(value, datetime):
                return value.date()
            return datetime.strptime(str(value).strip(), "%d-%b-%Y").date()
    return None
