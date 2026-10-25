"""News headlines about a company: Google News RSS (primary) and GDELT DOC 2.0 (optional).

Both are free and keyless. GDELT allows one request every 5 seconds per IP and throttles for a while after
that; Google News RSS is steadier. Only headline, publisher, date and link are kept, never article bodies.
"""

import time
from xml.etree import ElementTree
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import httpx

GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SUPPLY_TERMS = "(supplier OR supply OR contract OR order OR deal OR awarded)"


def google_news_query(name: str, days: int = 90) -> str:
    """Search string for supply-chain news about one company in the last `days` days."""
    return f'"{name}" {SUPPLY_TERMS} when:{days}d'


def parse_rss(xml_text: str) -> list[dict]:
    """Items of a Google News RSS feed: {url, title, source, published_at (ISO date or None)}."""
    root = ElementTree.fromstring(xml_text)
    articles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = item.find("source")
        publisher = (source.text or "").strip() if source is not None else ""
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")].strip()
        articles.append(
            {
                "url": (item.findtext("link") or "").strip(),
                "title": title,
                "source": publisher or "unknown",
                "published_at": _iso_date(item.findtext("pubDate")),
            }
        )
    return [article for article in articles if article["url"] and article["title"]]


def _iso_date(text: str | None) -> str | None:
    """RFC 822 date -> 'YYYY-MM-DD' (None when missing or malformed)."""
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


def google_news(name: str, days: int = 90, client: httpx.Client | None = None) -> list[dict]:
    """Recent supply-chain headlines about a company."""
    params = {"q": google_news_query(name, days), "hl": "en-US", "gl": "US", "ceid": "US:en"}
    http = client or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response = http.get(GOOGLE_NEWS_URL, params=params)
    response.raise_for_status()
    return parse_rss(response.text)


def gdelt(name: str, days: int = 90, max_records: int = 50) -> list[dict]:
    """Same search on GDELT; returns [] when GDELT throttles us (HTTP 429 or a plain-text answer)."""
    query = f'"{name}" {SUPPLY_TERMS} sourcelang:english'
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records, "timespan": f"{days}d"}
    response = httpx.get(GDELT_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    time.sleep(6)
    if response.status_code != 200 or not response.text.lstrip().startswith("{"):
        return []
    return [
        {
            "url": article["url"],
            "title": article["title"].strip(),
            "source": article.get("domain", "unknown"),
            "published_at": _gdelt_date(article.get("seendate")),
        }
        for article in response.json().get("articles", [])
    ]


def _gdelt_date(text: str | None) -> str | None:
    """'20261008T120000Z' -> '2026-10-08'."""
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date().isoformat() if text else None
    except ValueError:
        return None


def is_recent(published_at: str | None, days: int, today: date | None = None) -> bool:
    """True when the article date is within the window (undated articles are kept)."""
    if not published_at:
        return True
    return (today or date.today()).toordinal() - date.fromisoformat(published_at).toordinal() <= days
