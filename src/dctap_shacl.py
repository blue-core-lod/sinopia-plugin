"""PyScript module: convert a DCTAP TSV file to SHACL, with localStorage caching."""

import csv
import io

from pyodide.http import pyfetch
from dctap2shacl import DCTap2SHACLTransformer
import js


def _key(version: str, filename: str) -> str:
    return f"dctap_shacl:{version}:{filename}"


def _read_cache(version: str, filename: str) -> str | None:
    return js.localStorage.getItem(_key(version, filename))


def _write_cache(version: str, filename: str, shacl: str) -> None:
    js.localStorage.setItem(_key(version, filename), shacl)


def _convert(tsv_text: str) -> str:
    """Parse TSV text and convert to SHACL Turtle via DCTap2SHACLTransformer."""
    rows = list(csv.DictReader(io.StringIO(tsv_text), delimiter="\t"))
    transformer = DCTap2SHACLTransformer()
    transformer.generate_shacl(rows)
    return transformer.graph.serialize(format="turtle")


async def view_as_html(filename: str) -> str:
    """Fetch a DCTAP TSV and return an HTML table via pandas."""
    import pandas as pd

    resp = await pyfetch(f"/sinopia/api/dctap/tsv?filename={filename}")
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status} fetching {filename}")

    tsv = await resp.string()
    df = pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str).fillna("")
    return df.to_html(
        classes="table table-sm table-striped table-bordered",
        index=False,
        border=0,
    )


def add_to_template_graph(shacl_turtle: str) -> int:
    """Append a SHACL Turtle string to the 'template' list in localStorage.

    Deduplicates by exact string match.  Returns the resulting list length.
    """
    import json
    raw = js.localStorage.getItem("template")
    items: list[str] = json.loads(raw) if raw else []
    if shacl_turtle not in items:
        items.append(shacl_turtle)
        js.localStorage.setItem("template", json.dumps(items))
    return len(items)


async def get_shacl(version: str, filename: str) -> str:
    """Return SHACL Turtle for a DCTAP file.

    Checks localStorage first (keyed by version + filename).
    On a cache miss, fetches the TSV from the server, converts it,
    and saves the result back to localStorage before returning.
    """
    cached = _read_cache(version, filename)
    if cached:
        return cached

    resp = await pyfetch(f"/sinopia/api/dctap/tsv?filename={filename}")
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status} fetching {filename}")

    tsv = await resp.string()
    shacl = _convert(tsv)
    _write_cache(version, filename, shacl)
    return shacl
