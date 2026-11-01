"""Tests of the demo page (role A): it is served, self-contained, and only calls routes that exist."""

import re

from app.main import app
from app.routers.ui import UI_DIR

SCRIPT = (UI_DIR / "ui.js").read_text(encoding="utf-8")
PAGE = (UI_DIR / "index.html").read_text(encoding="utf-8")


def _route_patterns() -> list[re.Pattern]:
    """Every API route of the OpenAPI schema as a regex: `/api/companies/{isin}` becomes `/api/companies/[^/]+`."""
    return [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in app.openapi()["paths"]]


def _called_paths() -> set[str]:
    """API paths written in the script, with `${...}` placeholders replaced by a dummy segment."""
    paths = set()
    for literal in re.findall(r"[`\"](/api/[^`\"]*)[`\"]", SCRIPT):
        path = re.sub(r"\$\{[^}]*\}", "X", literal).split("?")[0]
        paths.add(path)
    return paths


def test_page_and_assets_are_served(api_fake):
    """`/` redirects to the page, the page and its two assets answer, anything else is 404."""
    assert api_fake.get("/", follow_redirects=False).headers["location"] == "/ui"
    page = api_fake.get("/ui")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert api_fake.get("/ui/ui.js").headers["content-type"].startswith("text/javascript")
    assert api_fake.get("/ui/ui.css").headers["content-type"].startswith("text/css")
    assert api_fake.get("/ui/main.py").status_code == 404
    assert api_fake.get("/ui/..%2Fmain.py").status_code == 404


def test_every_called_path_is_a_real_route():
    """A renamed route would break the demo silently: every path the script calls must exist."""
    called = _called_paths()
    patterns = _route_patterns()
    assert len(called) >= 12
    missing = [path for path in called if not any(pattern.match(path) for pattern in patterns)]
    assert not missing, f"the page calls routes that do not exist: {missing}"


def test_page_is_self_contained():
    """No CDN or external script: the demo works offline and inside the image."""
    for text in (PAGE, SCRIPT):
        assert not re.search(r"(src|href)=\"https?://", text)
    assert "fetch(path" in SCRIPT and "fetch(" not in SCRIPT.replace("fetch(path", "")


def test_data_is_never_inserted_as_html():
    """Text from the API goes through textContent: the script never assigns innerHTML."""
    assert "innerHTML" not in SCRIPT and "outerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT


def test_agent_tab_shows_the_verifier_reason_and_a_proxy_timeout():
    """A removed claim says why, and a 504 from Nginx (HTML body) still gives a readable message."""
    assert "claim.reason" in SCRIPT
    assert "response.status === 504" in SCRIPT
    assert "clearInterval(timer)" in SCRIPT


def test_api_key_is_kept_in_the_tab_only():
    """The agent key lives in sessionStorage, never in localStorage."""
    assert "localStorage" not in SCRIPT and "sessionStorage" in SCRIPT
