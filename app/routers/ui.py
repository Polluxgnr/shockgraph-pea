"""Demo interface (role A): one static page at /ui that calls the public API only, no build step.

The page is plain HTML, CSS and JavaScript: nothing to compile, no CDN, so it works offline and in the image.
`/` redirects to it, so opening http://localhost lands on the demo; Swagger stays at /docs.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
# Served files are an explicit allow-list: the path parameter can never reach another file on disk.
ASSETS = {"ui.css": "text/css", "ui.js": "text/javascript"}

router = APIRouter(include_in_schema=False)


@router.get("/")
def root() -> RedirectResponse:
    """Send a browser opening the bare host to the demo page."""
    return RedirectResponse(url="/ui")


@router.get("/ui")
def page() -> FileResponse:
    """The single page."""
    return FileResponse(UI_DIR / "index.html", media_type="text/html")


@router.get("/ui/{asset}")
def asset(asset: str) -> FileResponse:  # pylint: disable=redefined-outer-name
    """The page's stylesheet and script, and nothing else."""
    if asset not in ASSETS:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(UI_DIR / asset, media_type=ASSETS[asset])
