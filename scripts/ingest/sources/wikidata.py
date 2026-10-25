"""Wikidata SPARQL: company entity and products (P1056) looked up by ISIN (P946).

Public endpoint, no key. Queries are batched (VALUES with up to 150 ISINs) and never use the label
service, which is the slow part of the endpoint.
"""

import time

import httpx

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "shockgraph-pea/0.1 (student project; https://github.com/Polluxgnr/shockgraph-pea)"
BATCH_SIZE = 150

PRODUCTS_QUERY = """
SELECT ?isin ?company ?product ?label WHERE {
  VALUES ?isin { %s }
  ?company wdt:P946 ?isin .
  OPTIONAL { ?company wdt:P1056 ?product .
             OPTIONAL { ?product rdfs:label ?label FILTER(LANG(?label) = "en") } }
}
"""


def entity_id(uri: str | None) -> str | None:
    """'http://www.wikidata.org/entity/Q154037' -> 'Q154037'."""
    return uri.rsplit("/", 1)[-1] if uri else None


def build_query(isins: list[str]) -> str:
    """SPARQL for one batch. ISINs are validated upstream (12 alphanumeric characters), never free text."""
    if any(not (len(isin) == 12 and isin.isalnum()) for isin in isins):
        raise ValueError("ISINs must be 12 alphanumeric characters")
    return PRODUCTS_QUERY % " ".join(f'"{isin}"' for isin in isins)


def products_by_isin(isins: list[str], pause: float = 1.0) -> list[dict]:
    """Rows {isin, company (Q-id), product (Q-id or None), label (or None)} for every ISIN found."""
    rows: list[dict] = []
    with httpx.Client(
        timeout=120, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    ) as http:
        for start in range(0, len(isins), BATCH_SIZE):
            query = build_query(isins[start : start + BATCH_SIZE])
            for binding in _fetch(http, query)["results"]["bindings"]:
                rows.append(
                    {
                        "isin": binding["isin"]["value"],
                        "company": entity_id(binding["company"]["value"]),
                        "product": entity_id(binding.get("product", {}).get("value")),
                        "label": binding.get("label", {}).get("value"),
                    }
                )
            time.sleep(pause)
    return rows


def _fetch(http: httpx.Client, query: str, attempts: int = 5) -> dict:
    """Run one SPARQL query, retrying on throttling, server errors and dropped connections."""
    for attempt in range(attempts):
        try:
            response = http.get(ENDPOINT, params={"query": query})
        except httpx.TransportError:
            time.sleep(10 * (attempt + 1))
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(int(response.headers.get("retry-after", 10 * (attempt + 1))))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Wikidata kept failing after {attempts} attempts")
