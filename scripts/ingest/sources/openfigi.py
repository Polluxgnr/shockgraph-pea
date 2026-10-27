"""OpenFIGI: ISIN to exchange listings, used to find each company's Yahoo Finance symbol.

Free API. Without a key: 25 requests per minute, 10 ISINs per request. With OPENFIGI_API_KEY:
25 requests per 6 seconds, 100 ISINs per request. Answers are cached in a JSON file.
"""

import json
import time
from pathlib import Path

import httpx

URL = "https://api.openfigi.com/v3/mapping"

# Bloomberg exchange code -> Yahoo Finance suffix ("" for US composite listings).
YAHOO_SUFFIX = {
    "US": "",
    "FP": ".PA",
    "GY": ".DE",
    "NA": ".AS",
    "BB": ".BR",
    "IM": ".MI",
    "SM": ".MC",
    "ID": ".IR",
    "PL": ".LS",
    "AV": ".VI",
    "FH": ".HE",
    "DC": ".CO",
    "SS": ".ST",
    "NO": ".OL",
    "PW": ".WA",
    "LN": ".L",
    "SW": ".SW",
    "SE": ".SW",
    "JT": ".T",
    "HK": ".HK",
    "CN": ".TO",
    "AT": ".AX",
    "KS": ".KS",
    "TT": ".TW",
    "IS": ".NS",
    "SP": ".SI",
    "BZ": ".SA",
    "MM": ".MX",
    "SJ": ".JO",
    "CG": ".SS",
    "CS": ".SZ",
    "AB": ".SR",
    "QD": ".QA",
    "TI": ".IS",
    "KK": ".KW",
    "CI": ".SN",
    "EY": ".CA",
    "GA": ".AT",
    "CP": ".PR",
    "HB": ".BD",
    "IT": ".TA",
    "TB": ".BK",
    "MK": ".KL",
    "IJ": ".JK",
    "PM": ".PS",
    "NZ": ".NZ",
}

# Preferred exchanges per country of the ISIN (home listing first).
HOME_EXCHANGES = {
    "US": ["US"],
    "FR": ["FP"],
    "DE": ["GY"],
    "NL": ["NA"],
    "BE": ["BB"],
    "IT": ["IM"],
    "ES": ["SM"],
    "IE": ["ID", "LN", "US"],
    "PT": ["PL"],
    "AT": ["AV"],
    "FI": ["FH"],
    "DK": ["DC"],
    "SE": ["SS"],
    "NO": ["NO"],
    "PL": ["PW"],
    "GB": ["LN"],
    "JE": ["LN", "US"],
    "CH": ["SW", "SE"],
    "JP": ["JT"],
    "HK": ["HK"],
    "CA": ["CN"],
    "AU": ["AT"],
    "KR": ["KS"],
    "TW": ["TT"],
    "IN": ["IS"],
    "SG": ["SP"],
    "BR": ["BZ"],
    "MX": ["MM"],
    "ZA": ["SJ"],
    "LU": ["FP", "NA", "GY", "US"],
    "CN": ["CG", "CS", "HK"],
    "SA": ["AB"],
    "QA": ["QD"],
    "TR": ["TI"],
    "KW": ["KK"],
    "CL": ["CI"],
    "EG": ["EY"],
    "GR": ["GA"],
    "CZ": ["CP"],
    "HU": ["HB"],
    "IL": ["IT"],
    "TH": ["TB"],
    "MY": ["MK"],
    "ID": ["IJ"],
    "PH": ["PM"],
    "NZ": ["NZ"],
    "KY": ["HK", "US"],
    "BM": ["HK", "US"],
}
# No "US" here: for a non-US ISIN the US line is usually an OTC quote that barely trades (Tencent -> TCTZF),
# which would pull its betas towards 0. No ticker is better than an OTC ticker: C1 skips companies without one.
FALLBACK_EXCHANGES = ["LN", "FP", "GY", "NA", "HK"]


def yahoo_symbol(isin: str, listings: list[dict], listing_country: str | None = None) -> tuple[str | None, str | None]:
    """Pick the home listing of an ISIN and return (Yahoo symbol, FIGI), or (None, None).

    Order: exchanges of the country where the index provider says the share trades (`listing_country`, ISO
    code), then those of the ISIN country, then a short non-US fallback. Aegon is incorporated in Bermuda but
    trades in Amsterdam: AGN.AS, not the OTC line AEGOF. Only equity listings with a known Yahoo suffix count.
    """
    by_exchange = {}
    for listing in listings:
        if listing.get("marketSector") == "Equity" and listing.get("exchCode") in YAHOO_SUFFIX:
            by_exchange.setdefault(listing["exchCode"], listing)
    order = HOME_EXCHANGES.get(listing_country or "", []) + HOME_EXCHANGES.get(isin[:2], []) + FALLBACK_EXCHANGES
    if listing_country and listing_country != "US" and not isin.startswith("US"):
        order = [exchange for exchange in order if exchange != "US"]  # it trades elsewhere: a US line is OTC
    for exchange in order:
        if exchange in by_exchange:
            listing = by_exchange[exchange]
            ticker = str(listing["ticker"]).replace("/", "-").replace(" ", "-")
            if exchange == "HK":
                ticker = ticker.zfill(4)
            return ticker + YAHOO_SUFFIX[exchange], listing.get("figi")
    return None, None


def all_symbols(listings: list[dict]) -> list[str]:
    """Yahoo symbols of every equity listing (home and secondary), to resolve member lists by symbol."""
    symbols = []
    for listing in listings:
        if listing.get("marketSector") == "Equity" and listing.get("exchCode") in YAHOO_SUFFIX:
            symbols.append(
                str(listing["ticker"]).replace("/", "-").replace(" ", "-") + YAHOO_SUFFIX[listing["exchCode"]]
            )
    return symbols


class OpenFigiClient:
    """Batched, rate-limited, cached ISIN mapping."""

    def __init__(self, api_key: str = "", cache_path: Path | None = None) -> None:
        self.api_key = api_key
        self.cache_path = cache_path
        self.cache: dict[str, list[dict]] = {}
        if cache_path and cache_path.exists():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.batch_size = 100 if api_key else 10
        self.pause = 6.5 / 25 if api_key else 60 / 25 + 0.1

    def map_isins(self, isins: list[str]) -> dict[str, list[dict]]:
        """Return every listing of each ISIN (empty list when unknown), calling the API only for new ones."""
        missing = [isin for isin in dict.fromkeys(isins) if isin not in self.cache]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        with httpx.Client(timeout=60, headers=headers) as http:
            for start in range(0, len(missing), self.batch_size):
                batch = missing[start : start + self.batch_size]
                jobs = [{"idType": "ID_ISIN", "idValue": isin} for isin in batch]
                response = self._post(http, jobs)
                for isin, answer in zip(batch, response):
                    self.cache[isin] = answer.get("data", [])
                self._save()
                time.sleep(self.pause)
        return {isin: self.cache.get(isin, []) for isin in isins}

    def _post(self, http: httpx.Client, jobs: list[dict]) -> list[dict]:
        """POST one batch, retrying on rate limits, server errors and timeouts."""
        for attempt in range(5):
            try:
                response = http.post(URL, json=jobs)
            except httpx.TransportError:
                time.sleep(10 * (attempt + 1))
                continue
            if response.status_code == 429:
                time.sleep(int(response.headers.get("ratelimit-reset", "30")) + 1)
                continue
            if response.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("OpenFIGI kept failing (429 or timeouts)")

    def _save(self) -> None:
        """Persist the cache after each batch so an interrupted run resumes."""
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.cache), encoding="utf-8")
            temporary.replace(self.cache_path)  # atomic: an interrupted run never leaves half a file
