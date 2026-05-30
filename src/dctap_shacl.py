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
