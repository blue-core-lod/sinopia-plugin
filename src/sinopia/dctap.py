"""Fetch DCTAP TSV filenames and content from a bf-interop/DCTap GitHub release zip."""

import io
import zipfile

import httpx

DCTAP_REPO = "bf-interop/DCTap"

_template_cache: dict[str, list[dict]] = {}
_zip_cache: dict[str, bytes] = {}


async def _fetch_zip(version: str) -> bytes:
    if version in _zip_cache:
        return _zip_cache[version]
    url = f"https://api.github.com/repos/{DCTAP_REPO}/zipball/refs/tags/{version}"
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    _zip_cache[version] = resp.content
    return resp.content


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
    """Return one entry per non-Prefixes TSV file for the given tag."""
    if version in _template_cache:
        return _template_cache[version]
    zip_bytes = await _fetch_zip(version)
    result = _parse_zip(zip_bytes)
    _template_cache[version] = result
    return result


async def fetch_tsv_content(version: str, filename: str) -> str | None:
    """Return the raw text of a named TSV file from the zip, or None if not found."""
    zip_bytes = await _fetch_zip(version)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 3 and parts[2] == filename:
                return zf.read(name).decode("utf-8-sig")
    return None
