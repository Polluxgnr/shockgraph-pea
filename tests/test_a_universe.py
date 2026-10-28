"""Tests of the universe ingestion (role A, A1). No network: sources are faked or built in memory."""

from datetime import date

import openpyxl
import pytest

from scripts.ingest import universe
from scripts.ingest.sources import ssga
from scripts.ingest.sources.indexes import parse_market_cap
from scripts.ingest.sources.openfigi import all_symbols, yahoo_symbol

EEA = {"FR", "DE", "NL", "IE", "LU"}
NAMES = {"France": "FR", "Germany": "DE", "Netherlands": "NL", "Ireland": "IE", "United States": "US"}


def listing(exchange: str, ticker: str, sector: str = "Equity") -> dict:
    """One OpenFIGI listing."""
    return {"exchCode": exchange, "ticker": ticker, "marketSector": sector, "figi": f"BBG-{exchange}-{ticker}"}


# --- pure helpers ---------------------------------------------------------------------------------


def test_slug():
    """Names become stable upper-case codes."""
    assert universe.slug("Semiconductors & Semiconductor Equipment") == "SEMICONDUCTORS_SEMICONDUCTOR_EQUIPMENT"
    assert universe.slug("") == "UNASSIGNED"


@pytest.mark.parametrize(
    ("isin", "listing_country", "expected"),
    [
        ("FR0000120271", "France", True),
        ("NL0010273215", "Netherlands", True),
        ("IE00BTN1Y115", "United States", False),  # Irish-incorporated, US-listed (Medtronic-like)
        ("IE0001827041", "Ireland", True),
        ("US0378331005", "United States", False),
        ("GB0005405286", "United Kingdom", False),
        ("DE0007164600", None, True),
    ],
)
def test_pea_rule(isin, listing_country, expected):
    """EU/EEA incorporation and EU/EEA listing."""
    assert universe.is_pea_eligible(isin, listing_country, EEA, NAMES) is expected


def test_yahoo_symbol_prefers_home_exchange():
    """A French ISIN maps to its Paris listing even when a US line comes first."""
    listings = [listing("US", "TTFNF"), listing("GY", "TOTB"), listing("FP", "TTE")]
    assert yahoo_symbol("FR0000120271", listings) == ("TTE.PA", "BBG-FP-TTE")


def test_yahoo_symbol_formats_us_classes_and_hong_kong():
    """'BRK/B' becomes 'BRK-B'; Hong Kong codes are zero-padded."""
    assert yahoo_symbol("US0846707026", [listing("US", "BRK/B")])[0] == "BRK-B"
    assert yahoo_symbol("KYG875721634", [listing("HK", "700")])[0] == "0700.HK"


def test_yahoo_symbol_china_a_shares():
    """Shanghai and Shenzhen A-shares get the Yahoo .SS and .SZ suffixes."""
    assert yahoo_symbol("CNE000001R84", [listing("CH", "600519"), listing("CG", "600519")])[0] == "600519.SS"
    assert yahoo_symbol("CNE100000G29", [listing("CS", "000333")])[0] == "000333.SZ"


def test_all_symbols_lists_secondary_listings():
    """Airbus is home-listed in Amsterdam but the CAC 40 lists it as AIR.PA: both symbols are known."""
    assert all_symbols([listing("NA", "AIR"), listing("FP", "AIR"), listing("ZZ", "X")]) == ["AIR.AS", "AIR.PA"]


def test_yahoo_symbol_ignores_non_equity_and_unknown_exchanges():
    """No usable listing gives (None, None)."""
    assert yahoo_symbol("FR0000120271", [listing("FP", "X", sector="Corp"), listing("ZZ", "Y")]) == (None, None)


def test_parse_market_cap():
    """Nasdaq formats caps with commas."""
    assert parse_market_cap("5,074,761,240,500") == 5074761240500.0
    assert parse_market_cap("") is None and parse_market_cap("NA") is None


def test_normalise_and_world_proxy():
    """World = ACWI minus EM, renormalised."""
    acwi = {"US1": 0.6, "FR1": 0.2, "CN1": 0.2}
    world = universe.derived_world(acwi, {"CN1": 1.0})
    assert world == pytest.approx({"US1": 0.75, "FR1": 0.25})
    assert universe.normalise({"A": 0, "B": None}) == {}


def test_market_caps_are_calibrated_on_anchors_then_propagated():
    """ACWI scale from the anchors; a regional index scale from its overlap with ACWI."""
    weights = {
        "IDX_ACWI": {"A": 0.5, "B": 0.25, "C": 0.25},
        "IDX_EUROPE": {"C": 0.5, "D": 0.5},
    }
    caps = universe.estimate_market_caps(weights, {"A": 1000.0, "B": 500.0}, eur_usd=2.0)
    assert caps["A"] == pytest.approx(500.0)  # 1000 USD / 2
    assert caps["C"] == pytest.approx(250.0)  # 0.25 * 2000 / 2
    assert caps["D"] == pytest.approx(250.0)  # Europe total = 500 USD (C), D has the same weight


def test_calibrate_scale_without_overlap():
    """No common key: no scale."""
    assert universe.calibrate_scale({"A": 0.5}, {"B": 10.0}) is None


def test_members_by_cap_drops_unresolved_symbols():
    """Unknown symbols or caps are skipped and the rest renormalised."""
    weights = universe.members_by_cap(["AAA", "BBB", "CCC"], {"AAA": "I1", "BBB": "I2"}, {"I1": 3.0})
    assert weights == {"I1": 1.0}


def test_parse_holdings(tmp_path):
    """Header block, cash rows and duplicate share classes are handled; weights sum to 1."""
    book = openpyxl.Workbook()
    sheet = book.active
    rows = [
        ["Fund Name:", "SPDR test"],
        ["ISIN:", "IE00TEST0000"],
        ["Holdings As Of:", "22-Oct-2026"],
        [],
        [
            "ISIN",
            "SEDOL",
            "Security Name",
            "Currency",
            "Number of Shares",
            "Percent of Fund",
            "Trade Country Name",
            "Local Price",
            "Sector Classification",
            "Industry Classification",
            "Base Market Value",
        ],
        ["FR0000120271", "x", "TotalEnergies SE", "EUR", 1, 30.0, "France", 1, "Energy", "Oil, Gas", 300],
        ["US0378331005", "x", "Apple Inc.", "USD", 1, 50.0, "United States", 1, "Information Technology", "Tech", 500],
        ["US0378331005", "x", "Apple Inc.", "USD", 1, 10.0, "United States", 1, "Information Technology", "Tech", 100],
        ["CASH_EUR", "", "Euro cash", "EUR", 1, 10.0, "", 1, "", "", 100],
        ["Past performance is not a guarantee"],
    ]
    for row in rows:
        sheet.append(row)
    path = tmp_path / "holdings.xlsx"
    book.save(path)

    frame, as_of = ssga.parse_holdings(path)
    assert as_of == date(2026, 10, 22)
    assert frame["isin"].tolist() == ["US0378331005", "FR0000120271"]
    assert frame["weight"].tolist() == pytest.approx([0.6667, 0.3333], abs=1e-4)
    assert frame.loc[0, "market_value"] == 600


# --- load into the test database ------------------------------------------------------------------

TEST_DATA = {
    "countries": [{"code": "FR", "name": "France", "eu_eea": "true"}],
    "indexes": [{"code": "TEST-a-IDX", "name": "Test index", "provider": "test", "method": "test", "source": "test"}],
    "companies": [
        {
            "isin": isin,
            "name": f"Test company {isin}",
            "ticker": None,
            "figi": None,
            "currency": "EUR",
            "listing_country": "France",
            "country_code": "FR",
            "pea_eligible": True,
            "market_cap_eur": 1.0e9,
            "market_cap_method": "test",
            "sector": "Test sector A1",
            "sector_code": "TEST_A_SECTOR",
            "industry": "Test industry A1",
            "industry_code": "TEST_A_INDUSTRY",
        }
        for isin in ("ZZTEST000011", "ZZTEST000029", "ZZTEST000037")
    ],
    "constituents": [
        {"index": "TEST-a-IDX", "isin": "ZZTEST000011", "weight": 0.5},
        {"index": "TEST-a-IDX", "isin": "ZZTEST000029", "weight": 0.3},
        {"index": "TEST-a-IDX", "isin": "ZZTEST000037", "weight": 0.2},
    ],
    "funds": [
        {
            "isin": "ZZFUND000011",
            "name": "Test fund",
            "ticker": "ZZF.PA",
            "issuer": "test",
            "ter": "0.25",
            "replication": "synthetic",
            "pea_eligible": "true",
            "index": "TEST-a-IDX",
            "source_url": "",
            "checked": "test",
        }
    ],
    "as_of": "2026-10-22",
}

CLEANUP = """
MATCH (n) WHERE n.isin STARTS WITH 'ZZ' OR n.code IN ['TEST-a-IDX', 'TEST_A_SECTOR', 'TEST_A_INDUSTRY']
DETACH DELETE n
"""


@pytest.fixture
def loaded(neo4j_client):
    """Load the test universe twice (idempotency) and clean up afterwards."""
    neo4j_client.write(CLEANUP)
    universe.load(neo4j_client, TEST_DATA)
    universe.load(neo4j_client, TEST_DATA)
    yield neo4j_client
    neo4j_client.write(CLEANUP)


@pytest.mark.neo4j
def test_load_is_idempotent(loaded):
    """Two loads give one node and one link of each."""
    row = loaded.read("""
        MATCH (i:Index {code: 'TEST-a-IDX'})-[r:CONSTITUENT]->(c:Company)
        WITH i, count(r) AS links, sum(r.weight) AS total
        MATCH (f:Fund {isin: 'ZZFUND000011'})-[t:TRACKS]->(i)
        RETURN links, total, count(t) AS tracks, f.ter AS ter, f.pea_eligible AS pea
        """)[0]
    assert row == {"links": 3, "total": pytest.approx(1.0), "tracks": 1, "ter": 0.25, "pea": True}


@pytest.mark.neo4j
def test_load_links_industry_sector_and_country(loaded):
    """Each company is linked to its industry, sector and country exactly once."""
    rows = loaded.read("""
        MATCH (c:Company)-[:IN_INDUSTRY]->(:Industry {code: 'TEST_A_INDUSTRY'})-[:PART_OF]->(s:Sector)
        MATCH (c)-[:HEADQUARTERED_IN]->(k:Country)
        RETURN count(*) AS rows, collect(DISTINCT s.code) AS sectors, collect(DISTINCT k.code) AS countries
        """)[0]
    assert rows == {"rows": 3, "sectors": ["TEST_A_SECTOR"], "countries": ["FR"]}


@pytest.mark.neo4j
def test_stale_constituents_are_removed(loaded):
    """A company that left the index loses its CONSTITUENT link on the next load."""
    newer = dict(TEST_DATA, as_of="2026-10-23", constituents=TEST_DATA["constituents"][:2])
    universe.load(loaded, newer)
    count = loaded.read("MATCH (:Index {code: 'TEST-a-IDX'})-[r:CONSTITUENT]->() RETURN count(r) AS links")
    assert count[0]["links"] == 2


# --- review of #24 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("isin", "listings", "expected"),
    [
        # real OpenFIGI answers: the home line must win over the US OTC line
        ("KYG875721634", [listing("US", "TCTZF"), listing("GR", "NNND"), listing("HK", "700")], "0700.HK"),
        ("GRS003003035", [listing("US", "NBGIF"), listing("GR", "NAGF"), listing("GA", "ETE")], "ETE.AT"),
        ("CZ0005112300", [listing("US", "CZAVF"), listing("GR", "CEZ"), listing("CP", "CEZ")], "CEZ.PR"),
        ("TH0646010Z18", [listing("US", "PTTGF"), listing("TB", "PTT")], "PTT.BK"),
    ],
)
def test_home_listing_beats_us_otc(isin, listings, expected):
    """Tencent, National Bank of Greece, CEZ, PTT: home exchange, not the OTC quote."""
    assert yahoo_symbol(isin, listings)[0] == expected


def test_listing_country_comes_first():
    """Incorporated in Bermuda, trades in Amsterdam / Singapore: the listing country wins over the ISIN country."""
    aegon = [listing("US", "AEGOF"), listing("NA", "AGN")]
    assert yahoo_symbol("BMG0112X1056", aegon, listing_country="NL")[0] == "AGN.AS"
    jardine = [listing("US", "JARLF"), listing("SP", "J36")]
    assert yahoo_symbol("BMG507361001", jardine, listing_country="SG")[0] == "J36.SI"
    # trades in Hong Kong per the index, no HK line in OpenFIGI: no ticker rather than the OTC line
    assert yahoo_symbol("BMG507361001", [listing("US", "JARLF")], listing_country="HK") == (None, None)


def test_no_us_fallback_for_non_us_isins():
    """A non-US ISIN with only a US line gets no ticker (an OTC price would flatten its betas)."""
    assert yahoo_symbol("AEA000201011", [listing("US", "ABCXF")]) == (None, None)
    assert yahoo_symbol("US0378331005", [listing("US", "AAPL")])[0] == "AAPL"


@pytest.mark.neo4j
def test_companies_carry_the_date_of_their_last_holdings_file(loaded):
    """A company that left every index keeps its old as_of, so queries can filter the current universe."""
    newer = dict(TEST_DATA, as_of="2026-10-23", companies=TEST_DATA["companies"][:2], constituents=[])
    universe.load(loaded, newer)
    rows = loaded.read(
        "MATCH (c:Company) WHERE c.isin STARTS WITH 'ZZTEST' RETURN c.isin AS isin, toString(c.as_of) AS as_of ORDER BY isin"
    )
    assert [row["as_of"] for row in rows] == ["2026-10-23", "2026-10-23", "2026-10-22"]


def test_no_fund_left_to_check():
    """Every ETF row was verified on an issuer document (review of #30): none may come back as 'to check'."""
    rows = universe.read_seed("funds.csv")
    assert not [row["ticker"] for row in rows if "to check" in row["checked"]]
    assert all(row["ter"] and row["replication"] in ("physical", "synthetic") for row in rows)
