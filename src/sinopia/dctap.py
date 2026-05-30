"""Fetch DCTAP TSV filenames from a bf-interop/DCTap GitHub release zip."""

import io
import zipfile

import httpx

DCTAP_REPO = "bf-interop/DCTap"

_cache: dict[str, list[dict]] = {}


def _parse_zip(zip_bytes: bytes) -> list[dict]:
    """Return one entry per non-Prefixes TSV file in the zip."""
    entries: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in sorted(zf.namelist()):
            parts = name.split("/")
            if len(parts) != 3:
                continue
            folder, filename = parts[1], parts[2]
            if not filename.endswith(".tsv") or "Prefixes" in filename:
                continue
            entries.append({
                "filename": filename,
                "type":     folder.replace(" DCTAP", ""),
            })
    return entries


async def fetch_templates(version: str) -> list[dict]:
    """Download the DCTap zip for a given tag and return parsed template metadata.

    Results are cached in memory by version string.
    """
    if version in _cache:
        return _cache[version]

    url = f"https://api.github.com/repos/{DCTAP_REPO}/zipball/refs/tags/{version}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    result = _parse_zip(resp.content)
    _cache[version] = result
    return result
