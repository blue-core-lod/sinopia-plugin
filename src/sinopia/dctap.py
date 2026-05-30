"""Fetch and parse DCTAP TSV files from a bf-interop/DCTap GitHub release zip."""

import csv
import io
import zipfile

import httpx

DCTAP_REPO = "bf-interop/DCTap"

_cache: dict[str, list[dict]] = {}


def _parse_prefixes(tsv_text: str) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    return {
        row["Prefix"].strip(): row["Namespace"].strip()
        for row in reader
        if row.get("Prefix", "").strip() and row.get("Namespace", "").strip()
    }


def _expand_uri(raw: str, prefixes: dict[str, str]) -> str:
    raw = raw.strip()
    for prefix, ns in prefixes.items():
        if raw.startswith(prefix):
            return ns + raw[len(prefix):]
    return raw


def _expand_target(target: str, prefixes: dict[str, str]) -> list[str]:
    if not target.strip():
        return []
    return [_expand_uri(t, prefixes) for t in target.split(";") if t.strip()]


def _parse_shapes(tsv_text: str, prefixes: dict[str, str], group: str) -> list[dict]:
    """Return one dict per unique shapeID found in the TSV."""
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    seen: dict[str, dict] = {}
    for row in reader:
        shape_id = row.get("shapeID", "").strip()
        if not shape_id or shape_id in seen:
            continue
        seen[shape_id] = {
            "shape_id":  shape_id,
            "label":     row.get("shapeLabel", "").strip(),
            "targets":   _expand_target(row.get("target", ""), prefixes),
            "group":     group,
            "author":    "bf-interop",
            "note":      row.get("note", "").strip(),
        }
    return list(seen.values())


def _parse_zip(zip_bytes: bytes) -> list[dict]:
    templates: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        all_names = zf.namelist()

        # Build folder → prefix mapping from Prefixes TSVs
        prefix_map: dict[str, dict[str, str]] = {}
        for name in all_names:
            parts = name.split("/")
            if len(parts) == 3 and parts[2].endswith(".tsv") and "Prefixes" in parts[2]:
                folder = parts[1]
                text = zf.read(name).decode("utf-8-sig")
                prefix_map[folder] = _parse_prefixes(text)

        # Parse shape TSVs
        for name in sorted(all_names):
            parts = name.split("/")
            if len(parts) != 3:
                continue
            folder, filename = parts[1], parts[2]
            if not filename.endswith(".tsv") or "Prefixes" in filename:
                continue
            group = folder.replace(" DCTAP", "")
            prefixes = prefix_map.get(folder, {})
            text = zf.read(name).decode("utf-8-sig")
            templates.extend(_parse_shapes(text, prefixes, group))

    return templates


async def fetch_templates(version: str) -> list[dict]:
    """Download the DCTap zip for a given tag and return parsed template metadata.

    Results are cached in memory by version string.
    """
    if version in _cache:
        return _cache[version]

    url = f"https://api.github.com/repos/{DCTAP_REPO}/zipball/refs/tags/{version}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"Accept": "application/octet-stream"})
        resp.raise_for_status()

    result = _parse_zip(resp.content)
    _cache[version] = result
    return result
