import math
import os
import pathlib
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PLUGIN_DIR       = pathlib.Path(__file__).parent
BLUECORE_URL     = os.environ.get("BLUECORE_URL", "https://dev.bcld.info").rstrip("/")
ENVIRONMENT      = os.environ.get("ENVIRONMENT", "")
SINOPIA_VERSION  = os.environ.get("SINOPIA_VERSION", "4.0.0")

app = FastAPI(title="Sinopia Linked Data Editor", version=SINOPIA_VERSION)
app.mount("/static", StaticFiles(directory=str(PLUGIN_DIR / "src" / "static")), name="static")
templates = Jinja2Templates(directory=str(PLUGIN_DIR / "src" / "templates"))


_BF_VOCAB = "http://id.loc.gov/ontologies/bibframe/"
PAGE_SIZE = 10


def _format_date(iso_str: str) -> str:
    match iso_str:
        case "":
            return ""
        case _:
            try:
                dt = datetime.fromisoformat(iso_str)
                return dt.strftime(f"%b {dt.day}, %Y")
            except ValueError:
                return iso_str


def _get_label(result: dict) -> str:
    data = result.get("data", {})
    match data.get("http://www.w3.org/2000/01/rdf-schema#label"):
        case str(label) if label:
            return label
    match data.get("title"):
        case {"mainTitle": str(t)} if t:
            return t
        case [{"mainTitle": str(t)}, *_] if t:
            return t
    return result.get("uri", "")


def _get_types(result: dict) -> list[str]:
    data = result.get("data", {})
    match data.get("@type", ""):
        case str(raw) if raw:
            raw_list = [raw]
        case [*items]:
            raw_list = items
        case _:
            raw_list = []
    return [t if t.startswith("http") else _BF_VOCAB + t for t in raw_list if t]


def _page_range(current: int, total_pages: int) -> list:
    """Return page numbers and '...' sentinels for the paginator."""
    if total_pages <= 8:
        return list(range(1, total_pages + 1))
    window_start = max(1, min(current - 2, total_pages - 5))
    window_end = min(window_start + 5, total_pages)
    pages: list = []
    if window_start > 1:
        pages.append(1)
        if window_start > 2:
            pages.append("...")
    pages.extend(range(window_start, window_end + 1))
    if window_end < total_pages:
        if window_end < total_pages - 1:
            pages.append("...")
        pages.append(total_pages)
    return pages


def _process_results(results: list[dict]) -> list[dict]:
    return [
        {
            "label": _get_label(r),
            "uri": r.get("uri", ""),
            "uuid": r.get("uuid", ""),
            "types": _get_types(r),
            "modified": _format_date(r.get("updated_at", "")),
            "group": "Blue Core",
        }
        for r in results
    ]


# ─── Library of Congress ────────────────────────────────────────────────────

_LOC_URI_TYPES: dict[str, str] = {
    "/resources/works/":     _BF_VOCAB + "Work",
    "/resources/instances/": _BF_VOCAB + "Instance",
    "/authorities/names/":   "http://www.loc.gov/mads/rdf/v1#Authority",
    "/authorities/subjects/":"http://www.loc.gov/mads/rdf/v1#Topic",
}


def _loc_types_from_uri(uri: str) -> list[str]:
    return [t for path, t in _LOC_URI_TYPES.items() if path in uri]


def _parse_loc_entry(entry: list) -> dict | None:
    label = uri = modified = ""
    for child in entry[2:]:
        if not isinstance(child, list):
            continue
        attrs = child[1] if len(child) > 1 and isinstance(child[1], dict) else {}
        match child[0]:
            case "atom:title":
                label = child[-1] if isinstance(child[-1], str) else ""
            case "atom:link" if attrs.get("rel") == "alternate" and "type" not in attrs:
                uri = attrs.get("href", "")
            case "atom:updated":
                raw = child[-1] if isinstance(child[-1], str) else ""
                modified = _format_date(raw[:10]) if raw else ""
    if not uri:
        return None
    return {
        "label": label,
        "uri": uri,
        "uuid": "",
        "types": _loc_types_from_uri(uri),
        "modified": modified,
        "group": "Library of Congress",
    }


def _parse_loc_feed(data: list) -> tuple[list[dict], int]:
    total = 0
    results: list[dict] = []
    for child in data[2:]:
        if not isinstance(child, list):
            continue
        match child[0]:
            case "opensearch:totalResults":
                try:
                    total = int(child[-1])
                except (ValueError, TypeError):
                    pass
            case "atom:entry":
                entry = _parse_loc_entry(child)
                if entry:
                    results.append(entry)
    return results, total


@app.get("/")
async def root():
    return {
        "message": "Sinopia Plugin",
        "version": "4.0.0",
        "bluecore_url": BLUECORE_URL,
    }


@app.get("/sinopia/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "environment": ENVIRONMENT,
            "active_nav": "dashboard",
            "recent_searches": [],
            "sinopia_version": SINOPIA_VERSION,
        },
    )


@app.get("/sinopia/api/resource/{resource_id}")
async def proxy_resource(resource_id: str):
    """Proxy JSON-LD from the BCLD API to avoid browser CORS issues."""
    url = f"{BLUECORE_URL}/works/{resource_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                url,
                headers={"Accept": "application/ld+json, application/json;q=0.9"},
                follow_redirects=True,
            )
            resp.raise_for_status()
            return JSONResponse(
                content=resp.json(),
                headers={"Cache-Control": "max-age=60"},
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"BCLD API error: {exc.response.status_code}",
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"BCLD API unreachable: {exc}")


@app.get("/sinopia/load", response_class=HTMLResponse)
async def load_rdf(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="load_rdf.html",
        context={
            "environment": ENVIRONMENT,
            "active_nav": "actions",
        },
    )


@app.get("/sinopia/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", source: str = "bluecore", page: int = 1):
    results: list[dict] = []
    total = 0
    error: str | None = None
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    match source:
        case "bluecore" if q:
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.get(
                        f"{BLUECORE_URL}/api/search/",
                        params={"q": q, "type": "works", "limit": PAGE_SIZE, "offset": offset},
                        headers={"Accept": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results = _process_results(data.get("results", []))
                    total = data.get("total", 0)
                except httpx.HTTPStatusError as exc:
                    error = f"Search API error: {exc.response.status_code}"
                except httpx.RequestError as exc:
                    error = f"Search API unreachable: {exc}"
                except Exception as exc:
                    error = f"Unexpected error: {type(exc).__name__}: {exc}"
        case "loc" if q:
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.get(
                        "https://id.loc.gov/search/",
                        params={"q": q, "format": "json", "start": offset + 1, "count": PAGE_SIZE},
                        headers={"Accept": "application/json"},
                    )
                    resp.raise_for_status()
                    results, total = _parse_loc_feed(resp.json())
                except httpx.HTTPStatusError as exc:
                    error = f"LoC API error: {exc.response.status_code}"
                except httpx.RequestError as exc:
                    error = f"LoC API unreachable: {exc}"
                except Exception as exc:
                    error = f"Unexpected error: {type(exc).__name__}: {exc}"

    total_pages = math.ceil(total / PAGE_SIZE) if total else 0

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "environment": ENVIRONMENT,
            "active_nav": "search",
            "search_q": q,
            "search_source": source,
            "results": results,
            "total": f"{total:,}",
            "error": error,
            "page": page,
            "total_pages": total_pages,
            "page_range": _page_range(page, total_pages),
            "first_result": offset + 1 if results else 0,
            "last_result": f"{(offset + len(results)):,}",
        },
    )


@app.get("/sinopia/editor", response_class=HTMLResponse)
async def editor_new(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "resource_id": "",
            "bluecore_url": BLUECORE_URL,
            "environment": ENVIRONMENT,
            "active_nav": "editor",
            "sinopia_version": SINOPIA_VERSION,
        },
    )


@app.get("/sinopia/editor/conf.json")
async def pyscript_config():
    return FileResponse(PLUGIN_DIR / "src" / "conf.json", media_type="application/json")


@app.get("/sinopia/editor/src/main.py")
async def pyscript_main_py():
    return FileResponse(PLUGIN_DIR / "src" / "main.py", media_type="text/plain")


@app.get("/sinopia/editor/{resource_id}", response_class=HTMLResponse)
async def editor(request: Request, resource_id: str):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "resource_id": resource_id,
            "bluecore_url": BLUECORE_URL,
            "environment": ENVIRONMENT,
            "active_nav": "editor",
        },
    )
