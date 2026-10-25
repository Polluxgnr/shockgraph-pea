"""Step 1 (role A, A1): the investable universe.

Sources: SSGA daily holdings (index constituents with ISIN, weight, GICS sector and industry),
OpenFIGI (ISIN to home listing, mapped to a Yahoo symbol), Nasdaq API (Nasdaq-100 members and market
caps), Wikipedia (CAC 40 members), ECB (EUR/USD), and hand-checked seed files in `data/seed/`.
Creates Company, Fund, Index, Sector, Industry, Country and TRACKS, CONSTITUENT, IN_INDUSTRY,
PART_OF, HEADQUARTERED_IN. Idempotent: MERGE on constrained keys, stale links removed.
Run alone with: python -m scripts.seed_data --only universe
"""

import csv
import re
from datetime import date
from pathlib import Path

import pandas as pd

from app.config import Settings
from app.database.neo4j import Neo4jClient
from scripts.ingest.sources import indexes as index_sources
from scripts.ingest.sources import ssga
from scripts.ingest.sources.openfigi import OpenFigiClient, all_symbols, yahoo_symbol

SEED_DIR = Path("data/seed")
BATCH_SIZE = 1000
PRIMARY_INDEX = "IDX_ACWI"


# --- pure helpers (unit-tested) -----------------------------------------------------------------


def slug(text: str) -> str:
    """'Semiconductors & Semiconductor Equipment' -> 'SEMICONDUCTORS_SEMICONDUCTOR_EQUIPMENT'."""
    return re.sub(r"[^A-Z0-9]+", "_", str(text).upper()).strip("_") or "UNASSIGNED"


def read_seed(name: str, seed_dir: Path = SEED_DIR) -> list[dict]:
    """Rows of a CSV seed file as dictionaries."""
    with (seed_dir / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def is_pea_eligible(isin: str, listing_country: str | None, eea_codes: set[str], name_to_code: dict) -> bool:
    """Approximation of PEA eligibility: registered office in the EU/EEA and home listing in the EU/EEA.

    The ISIN prefix is the country of incorporation, a proxy for the registered office. Requiring an
    EU/EEA listing removes US companies incorporated in Ireland (e.g. Medtronic), which French brokers
    do not accept in a PEA.
    """
    if isin[:2] not in eea_codes:
        return False
    listing_code = name_to_code.get(str(listing_country or "").strip())
    return listing_code is None or listing_code in eea_codes


def normalise(weights: dict[str, float]) -> dict[str, float]:
    """Scale positive weights so that they sum to 1 (empty dict stays empty)."""
    positive = {key: value for key, value in weights.items() if value and value > 0}
    total = sum(positive.values())
    return {key: value / total for key, value in positive.items()} if total else {}


def calibrate_scale(weights: dict[str, float], caps: dict[str, float]) -> float | None:
    """Median of cap / weight over the keys present in both: the total float cap the weights imply."""
    ratios = sorted(caps[key] / weights[key] for key in weights.keys() & caps.keys() if weights[key] > 0)
    if not ratios:
        return None
    middle = len(ratios) // 2
    return ratios[middle] if len(ratios) % 2 else (ratios[middle - 1] + ratios[middle]) / 2


def estimate_market_caps(
    index_weights: dict[str, dict[str, float]], anchor_caps_usd: dict[str, float], eur_usd: float
) -> dict[str, float]:
    """Float market caps in EUR estimated from index weights.

    In a float-weighted index, weight_i = float_cap_i / total. The ACWI total is calibrated on real market
    caps (Nasdaq API); every other index total is then calibrated on its overlap with ACWI estimates.
    """
    caps_usd: dict[str, float] = {}
    order = [PRIMARY_INDEX] + [code for code in index_weights if code != PRIMARY_INDEX]
    for code in order:
        weights = index_weights.get(code, {})
        reference = anchor_caps_usd if code == PRIMARY_INDEX else caps_usd
        scale = calibrate_scale(weights, reference)
        if scale is None:
            continue
        for isin, weight in weights.items():
            caps_usd.setdefault(isin, weight * scale)
    return {isin: cap / eur_usd for isin, cap in caps_usd.items()}


def derived_world(acwi: dict[str, float], emerging: dict[str, float]) -> dict[str, float]:
    """MSCI World proxy: ACWI constituents that are not in MSCI EM, renormalised."""
    return normalise({isin: weight for isin, weight in acwi.items() if isin not in emerging})


def members_by_cap(symbols: list[str], symbol_to_isin: dict[str, str], caps: dict[str, float]) -> dict[str, float]:
    """Weights proportional to market cap for the members we can resolve to an ISIN with a cap."""
    weights = {}
    for symbol in symbols:
        isin = symbol_to_isin.get(symbol)
        if isin and caps.get(isin):
            weights[isin] = caps[isin]
    return normalise(weights)


# --- build ---------------------------------------------------------------------------------------


def load_holdings(index_rows: list[dict], raw_dir: Path, refresh: bool) -> tuple[dict[str, pd.DataFrame], date]:
    """Download and parse every SSGA-backed index; return the frames and the latest holdings date."""
    holdings: dict[str, pd.DataFrame] = {}
    as_of: date | None = None
    for row in index_rows:
        if row["method"] == "ssga_holdings":
            frame, file_date = ssga.parse_holdings(ssga.download(row["source"], raw_dir, refresh))
            holdings[row["code"]] = frame
            as_of = max(filter(None, [as_of, file_date]), default=None)
    return holdings, as_of or date.today()


def company_row(isin: str, row: pd.Series, listing: tuple, caps_eur: dict, countries: dict[str, dict]) -> dict:
    """One Company row ready for the COMPANIES query."""
    eea_codes = {code for code, country in countries.items() if country["eu_eea"] == "true"}
    name_to_code = {country["name"]: code for code, country in countries.items()}
    estimated = isin in caps_eur
    return {
        "isin": isin,
        "name": str(row["name"]).strip(),
        "ticker": listing[0],
        "figi": listing[1],
        "currency": row["currency"],
        "listing_country": row["listing_country"],
        "country_code": isin[:2] if isin[:2] in countries else None,
        "pea_eligible": is_pea_eligible(isin, row["listing_country"], eea_codes, name_to_code),
        "market_cap_eur": round(caps_eur[isin], 0) if estimated else None,
        "market_cap_method": "float_estimate_from_index_weights" if estimated else None,
        "sector": row["sector"],
        "sector_code": slug(row["sector"]),
        "industry": row["industry"],
        "industry_code": slug(row["industry"]),
    }


def build(settings: Settings, refresh: bool = False) -> dict:
    """Download and assemble everything; return the rows to load and a report."""
    countries = {row["code"]: row for row in read_seed("countries.csv")}
    index_rows = read_seed("indexes.csv")
    holdings, as_of = load_holdings(index_rows, Path(settings.data_dir) / "raw" / "ssga", refresh)
    companies = pd.concat(holdings.values()).drop_duplicates("isin", keep="first").set_index("isin")
    weights = {code: dict(zip(frame["isin"], frame["weight"])) for code, frame in holdings.items()}

    figi = OpenFigiClient(settings.openfigi_api_key, Path(settings.data_dir) / "cache" / "openfigi.json")
    listings = figi.map_isins(companies.index.tolist())
    symbols = {isin: yahoo_symbol(isin, found) for isin, found in listings.items()}
    # Home symbols first, then every other listing: member lists use the symbol of the main market.
    symbol_to_isin = {alias: isin for isin, found in listings.items() for alias in all_symbols(found)}
    symbol_to_isin.update({symbol: isin for isin, (symbol, _) in symbols.items() if symbol})

    nasdaq = {row["symbol"].replace("/", "-"): row["market_cap_usd"] for row in index_sources.nasdaq100()}
    nasdaq_caps = {symbol_to_isin[sym]: cap for sym, cap in nasdaq.items() if cap and sym in symbol_to_isin}
    eur_usd = index_sources.eur_usd()
    caps_eur = estimate_market_caps(weights, nasdaq_caps, eur_usd)

    weights["IDX_WORLD"] = derived_world(weights["IDX_ACWI"], weights.get("IDX_EM", {}))
    weights["IDX_NASDAQ100"] = members_by_cap(list(nasdaq), symbol_to_isin, nasdaq_caps)
    cac_symbols = index_sources.cac40()
    weights["IDX_CAC40"] = members_by_cap(cac_symbols, symbol_to_isin, caps_eur)

    company_rows = [
        company_row(isin, row, symbols.get(isin, (None, None)), caps_eur, countries)
        for isin, row in companies.iterrows()
    ]
    report = {
        "companies": len(company_rows),
        "pea_eligible": sum(row["pea_eligible"] for row in company_rows),
        "with_ticker": sum(1 for row in company_rows if row["ticker"]),
        "with_market_cap": len(caps_eur),
        "nasdaq100_resolved": f"{len(weights['IDX_NASDAQ100'])}/{len(nasdaq)}",
        "cac40_resolved": f"{len(weights['IDX_CAC40'])}/{len(cac_symbols)}",
        "eur_usd": eur_usd,
        "as_of": as_of.isoformat(),
    }
    return {
        "countries": list(countries.values()),
        "indexes": index_rows,
        "companies": company_rows,
        "constituents": [
            {"index": code, "isin": isin, "weight": round(weight, 8)}
            for code, members in weights.items()
            for isin, weight in members.items()
        ],
        "funds": read_seed("funds.csv"),
        "as_of": as_of.isoformat(),
        "report": report,
    }


# --- load ----------------------------------------------------------------------------------------

COUNTRIES = """
UNWIND $rows AS row
MERGE (k:Country {code: row.code})
SET k.name = row.name, k.eu_eea = row.eu_eea = 'true'
"""

SECTORS = """
UNWIND $rows AS row
MERGE (s:Sector {code: row.sector_code}) SET s.name = row.sector
MERGE (n:Industry {code: row.industry_code}) SET n.name = row.industry
MERGE (n)-[:PART_OF]->(s)
"""

COMPANIES = """
UNWIND $rows AS row
MERGE (c:Company {isin: row.isin})
SET c.name = row.name, c.ticker = row.ticker, c.figi = row.figi, c.currency = row.currency,
    c.listing_country = row.listing_country, c.pea_eligible = row.pea_eligible,
    c.market_cap_eur = row.market_cap_eur, c.market_cap_method = row.market_cap_method
WITH c, row
OPTIONAL MATCH (c)-[old:IN_INDUSTRY]->(previous:Industry) WHERE previous.code <> row.industry_code
DELETE old
WITH c, row
MATCH (n:Industry {code: row.industry_code})
MERGE (c)-[:IN_INDUSTRY]->(n)
WITH c, row
MATCH (k:Country {code: row.country_code})
MERGE (c)-[:HEADQUARTERED_IN]->(k)
"""

INDEXES = """
UNWIND $rows AS row
MERGE (i:Index {code: row.code})
SET i.name = row.name, i.provider = row.provider, i.method = row.method, i.source = row.source
"""

CONSTITUENTS = """
UNWIND $rows AS row
MATCH (i:Index {code: row.index})
MATCH (c:Company {isin: row.isin})
MERGE (i)-[r:CONSTITUENT]->(c)
SET r.weight = row.weight, r.as_of = date($as_of)
"""

STALE_CONSTITUENTS = """
MATCH (i:Index)-[r:CONSTITUENT]->()
WHERE i.code IN $codes AND r.as_of <> date($as_of)
DELETE r
"""

FUNDS = """
UNWIND $rows AS row
MERGE (f:Fund {isin: row.isin})
SET f.name = row.name, f.ticker = row.ticker, f.issuer = row.issuer, f.ter = toFloat(row.ter),
    f.replication = row.replication, f.pea_eligible = row.pea_eligible = 'true',
    f.source_url = row.source_url, f.checked = row.checked
WITH f, row
OPTIONAL MATCH (f)-[old:TRACKS]->(previous:Index) WHERE previous.code <> row.index
DELETE old
WITH f, row
MATCH (i:Index {code: row.index})
MERGE (f)-[:TRACKS]->(i)
"""


def batches(rows: list[dict], size: int = BATCH_SIZE):
    """Yield consecutive slices of `rows`."""
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load(client: Neo4jClient, data: dict) -> None:
    """Write the assembled universe into Neo4j, batch by batch."""
    client.write(COUNTRIES, {"rows": data["countries"]})
    client.write(SECTORS, {"rows": _distinct_industries(data["companies"])})
    for chunk in batches(data["companies"]):
        client.write(COMPANIES, {"rows": chunk})
    client.write(INDEXES, {"rows": data["indexes"]})
    for chunk in batches(data["constituents"]):
        client.write(CONSTITUENTS, {"rows": chunk, "as_of": data["as_of"]})
    codes = sorted({row["index"] for row in data["constituents"]})
    client.write(STALE_CONSTITUENTS, {"codes": codes, "as_of": data["as_of"]})
    client.write(FUNDS, {"rows": data["funds"]})


def _distinct_industries(companies: list[dict]) -> list[dict]:
    """One row per (sector, industry) pair."""
    seen = {}
    for row in companies:
        seen.setdefault(
            row["industry_code"], {key: row[key] for key in ("sector", "sector_code", "industry", "industry_code")}
        )
    return list(seen.values())


def run(client: Neo4jClient, settings: Settings) -> dict:
    """Build the universe from the sources, load it, and return the report."""
    data = build(settings)
    load(client, data)
    return data["report"]
