import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sinopia.bluecore import _page_range, _process_results
from sinopia.config import (
    _BCLD_HEADERS,
    BF_INTEROP_VERSION,
    BLUECORE_URL,
    ENVIRONMENT,
    PAGE_SIZE,
    PLUGIN_DIR,
    SINOPIA_VERSION,
)
from sinopia.dctap import fetch_templates, fetch_tsv_content
from sinopia.loc import _parse_loc_feed
from sinopia.rdf import _detect_format, _parse_rdf, _shacl_violations

app = FastAPI(title="Sinopia Linked Data Editor", version=SINOPIA_VERSION)
app.mount("/static", StaticFiles(directory=str(PLUGIN_DIR / "src" / "static")), name="static")
templates = Jinja2Templates(directory=str(PLUGIN_DIR / "src" / "templates"))


@app.get("/")
async def root():
    return {
        "message": "Sinopia Plugin",
        "version": SINOPIA_VERSION,
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


@app.get("/sinopia/templates", response_class=HTMLResponse)
async def resource_templates(request: Request):
    rt_list: list[dict] = []
    fetch_error: str | None = None
    if BF_INTEROP_VERSION:
        try:
            rt_list = await fetch_templates(BF_INTEROP_VERSION)
        except Exception as exc:
            fetch_error = f"Could not load templates ({BF_INTEROP_VERSION}): {exc}"
    return templates.TemplateResponse(
        request=request,
        name="resource_templates.html",
        context={
            "environment":        ENVIRONMENT,
            "active_nav":         "templates",
            "templates":          rt_list,
            "bf_interop_version": BF_INTEROP_VERSION,
            "fetch_error":        fetch_error,
        },
    )


@app.get("/sinopia/api/dctap/tsv")
async def dctap_tsv(filename: str):
    """Return the raw TSV content of a named DCTAP file from the configured release."""
    if not BF_INTEROP_VERSION:
        raise HTTPException(status_code=503, detail="BF_INTEROP_VERSION not configured")
    content = await fetch_tsv_content(BF_INTEROP_VERSION, filename)
    if content is None:
        raise HTTPException(status_code=404, detail=f"{filename} not found in {BF_INTEROP_VERSION}")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content, media_type="text/tab-separated-values")


@app.get("/sinopia/api/resource/{resource_id}")
async def proxy_resource(resource_id: str):
    """Proxy JSON-LD from the BCLD API to avoid browser CORS issues."""
    url = f"{BLUECORE_URL}/works/{resource_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                url,
                params={"is_expanded": "true"},
                headers={"Accept": "application/ld+json, application/json;q=0.9", **_BCLD_HEADERS},
                follow_redirects=True,
            )
            resp.raise_for_status()
            body = resp.json()
            resource_data = body.get("data", body) if isinstance(body, dict) else body
            return JSONResponse(
                content=resource_data,
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


@app.post("/sinopia/load", response_class=HTMLResponse)
async def load_rdf_post(
    request: Request,
    rdf: str = Form(default=""),
    base_uri: str = Form(default=""),
    rdf_url: str = Form(default=""),
    shapes_url: str = Form(default=""),
):
    rdf_content = rdf.strip()
    rdf_format = "turtle"
    fetch_error: str | None = None

    if rdf_url and not rdf_content:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(
                    rdf_url,
                    headers={"Accept": "text/turtle, application/rdf+xml, application/ld+json, */*;q=0.8"},
                    follow_redirects=True,
                )
                resp.raise_for_status()
                rdf_content = resp.text
                rdf_format = _detect_format(resp.headers.get("content-type", ""), rdf_url)
            except httpx.HTTPStatusError as exc:
                fetch_error = f"HTTP {exc.response.status_code} fetching URL"
            except httpx.RequestError as exc:
                fetch_error = f"Could not reach URL: {exc}"

    parse_error: str | None = None
    triple_count = 0
    validation: dict | None = None

    if rdf_content and not fetch_error:
        data_graph, parse_error = _parse_rdf(rdf_content, base_uri.strip(), rdf_format)
        if not parse_error:
            triple_count = len(data_graph)
            if shapes_url.strip():
                validation = _shacl_violations(data_graph, shapes_url.strip())

    return templates.TemplateResponse(
        request=request,
        name="load_rdf.html",
        context={
            "environment": ENVIRONMENT,
            "active_nav": "actions",
            "rdf":          rdf,
            "rdf_url":      rdf_url,
            "base_uri":     base_uri,
            "shapes_url":   shapes_url,
            "fetch_error":  fetch_error,
            "parse_error":  parse_error,
            "triple_count": triple_count,
            "validation":   validation,
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
                        params={"q": q, "type": "works", "limit": PAGE_SIZE, "offset": offset, "is_expanded": "true"},
                        headers={"Accept": "application/json", **_BCLD_HEADERS},
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
            "environment":   ENVIRONMENT,
            "active_nav":    "search",
            "search_q":      q,
            "search_source": source,
            "results":       results,
            "total":         f"{total:,}",
            "error":         error,
            "page":          page,
            "total_pages":   total_pages,
            "page_range":    _page_range(page, total_pages),
            "first_result":  offset + 1 if results else 0,
            "last_result":   f"{(offset + len(results)):,}",
        },
    )


@app.get("/sinopia/editor", response_class=HTMLResponse)
async def editor_new(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "resource_id":    "",
            "bluecore_url":   BLUECORE_URL,
            "environment":    ENVIRONMENT,
            "active_nav":     "editor",
            "sinopia_version": SINOPIA_VERSION,
        },
    )


@app.get("/sinopia/editor/conf.json")
async def pyscript_config():
    return FileResponse(PLUGIN_DIR / "src" / "conf.json", media_type="application/json")


@app.get("/sinopia/editor/src/main.py")
async def pyscript_main_py():
    return FileResponse(PLUGIN_DIR / "src" / "main.py", media_type="text/plain")


@app.get("/sinopia/dctap-shacl.py")
async def dctap_shacl_module():
    return FileResponse(PLUGIN_DIR / "src" / "dctap_shacl.py", media_type="text/plain")


@app.get("/sinopia/editor/{resource_id}", response_class=HTMLResponse)
async def editor(request: Request, resource_id: str):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "resource_id":  resource_id,
            "bluecore_url": BLUECORE_URL,
            "environment":  ENVIRONMENT,
            "active_nav":   "editor",
        },
    )
