"""Fetch DCTAP templates and content from pluggable sources.

Two source layouts are supported:

* **bf-interop release zip** — a GitHub release tag of ``bf-interop/DCTap``.
  Each template is its own tab-separated file inside the zip, one shape per
  file.  The source identifier is the release tag (e.g. ``v0.3.0``).

* **marva-profiles CSV** — a single comma-separated file in
  ``lcnetdev/marva-profiles`` holding many shapes, grouped by ``shapeID``
  (continuation rows leave the shape-level columns blank).  The source
  identifier is :data:`MARVA_SOURCE`.

Both sources expose the same public API — :func:`fetch_templates` and
:func:`fetch_tsv_content` — and always return DCTAP rows as tab-separated
text using the column names the ``dctap2shacl`` transformer expects, so the
downstream conversion/rendering code is source-agnostic.
"""

import csv
import io
import itertools
import zipfile

import httpx

DCTAP_REPO = "bf-interop/DCTap"

#: Source identifier for the single marva-profiles DCTAP CSV.
MARVA_SOURCE = "marva-prod"

#: The marva shape whose ``valueShape`` rows enumerate the top-level starting
#: points of the profile outline.
MARVA_ROOT_SHAPE = "startingpoint:index"

#: How many levels deep the outline is built: the starting points (level 1) and
#: the shapes they reference (level 2).  Deeper references are not expanded.
MARVA_MAX_DEPTH = 2
MARVA_CSV_URL = (
    "https://raw.githubusercontent.com/lcnetdev/marva-profiles/main/marva-prod/dctap.csv"
)

_template_cache: dict[str, list[dict]] = {}
_zip_cache: dict[str, bytes] = {}
_csv_cache: dict[str, str] = {}


# ── bf-interop release zip ────────────────────────────────────────────────────

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


async def _bf_interop_templates(version: str) -> list[dict]:
    zip_bytes = await _fetch_zip(version)
    return _parse_zip(zip_bytes)


async def _bf_interop_content(version: str, filename: str) -> str | None:
    zip_bytes = await _fetch_zip(version)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 3 and parts[2] == filename:
                return zf.read(name).decode("utf-8-sig")
    return None


# ── marva-profiles CSV ────────────────────────────────────────────────────────

async def _fetch_csv(url: str = MARVA_CSV_URL) -> str:
    if url in _csv_cache:
        return _csv_cache[url]
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")
    _csv_cache[url] = text
    return text


def _parse_marva_csv(csv_text: str) -> list[dict]:
    """Normalise the marva CSV into DCTAP rows the transformer understands.

    The marva layout leaves ``shapeID``/``shapeLabel``/``resourceURI`` blank on
    every row after the first of a shape, so those columns are forward-filled.
    ``resourceURI`` is renamed to ``target`` (the column name dctap2shacl reads
    for ``sh:targetClass``).
    """
    rows: list[dict] = []
    shape_id = shape_label = target = ""
    for raw in csv.DictReader(io.StringIO(csv_text)):
        if (raw.get("shapeID") or "").strip():
            shape_id    = raw["shapeID"].strip()
            shape_label = (raw.get("shapeLabel") or "").strip()
            target      = (raw.get("resourceURI") or "").strip()
        row = {k: v for k, v in raw.items() if k is not None and k != "resourceURI"}
        row["shapeID"]    = shape_id
        row["shapeLabel"] = shape_label
        row["target"]     = target
        rows.append(row)
    return rows


def _marva_templates(csv_text: str) -> list[dict]:
    """Return one entry per distinct shape in the marva CSV."""
    entries: list[dict] = []
    seen: set[str] = set()
    for row in _parse_marva_csv(csv_text):
        shape_id = row["shapeID"]
        if not shape_id or shape_id in seen:
            continue
        seen.add(shape_id)
        entries.append({
            "filename": shape_id,
            "type":     row["shapeLabel"] or shape_id,
        })
    return entries


def _marva_shape_tsv(csv_text: str, shape_id: str) -> str | None:
    """Return the rows of a single marva shape as tab-separated DCTAP text."""
    selected = [r for r in _parse_marva_csv(csv_text) if r["shapeID"] == shape_id]
    if not selected:
        return None
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(selected[0].keys()), delimiter="\t")
    writer.writeheader()
    writer.writerows(selected)
    return buf.getvalue()


def _marva_outline(csv_text: str, max_depth: int = MARVA_MAX_DEPTH) -> list[dict]:
    """Build a nested outline of marva shapes rooted at :data:`MARVA_ROOT_SHAPE`.

    The ``startingpoint:index`` shape lists the production starting points in its
    ``valueShape`` column; each of those shapes in turn references further shapes
    through *their* ``valueShape`` columns.  This walks that graph and returns a
    tree of nodes::

        {"uid": int, "shape_id": str, "label": str, "children": [node, ...]}

    Top-level nodes are the starting points (the ``valueShape`` entries of
    ``startingpoint:index``); a node's children are the shapes referenced by its
    own rows.  The tree is built ``max_depth`` levels deep (default
    :data:`MARVA_MAX_DEPTH` — starting points plus the shapes they reference);
    deeper references are not expanded.  ``uid`` is unique per node occurrence so
    collapse targets in the template never collide even when a shape appears
    under several parents.  A shape is never expanded inside its own ancestry, so
    cycles terminate.
    """
    by_shape: dict[str, list[dict]] = {}
    for row in _parse_marva_csv(csv_text):
        by_shape.setdefault(row["shapeID"], []).append(row)

    labels = {sid: (rows[0].get("shapeLabel") or sid) for sid, rows in by_shape.items()}
    counter = itertools.count()

    def _child_refs(rows: list[dict]) -> list[tuple]:
        """Yield ``(shape_id, label)`` pairs for every ``valueShape`` referenced.

        A ``valueShape`` cell may hold ``|``-separated alternatives (a SHACL
        ``sh:or``); each alternative becomes its own node.  A single reference
        takes the row's ``propertyLabel``; alternatives are labelled by their own
        shape label so they stay distinguishable.
        """
        refs: list[tuple] = []
        for row in rows:
            parts = [p.strip() for p in (row.get("valueShape") or "").split("|") if p.strip()]
            prop_label = (row.get("propertyLabel") or "").strip()
            for part in parts:
                if len(parts) == 1:
                    label = prop_label or labels.get(part, part)
                else:
                    label = labels.get(part, part)
                refs.append((part, label))
        return refs

    def _build(shape_id: str, label: str, ancestors: frozenset, depth: int) -> dict:
        children: list[dict] = []
        if depth < max_depth:
            for child, child_label in _child_refs(by_shape.get(shape_id, [])):
                if child in ancestors:
                    continue
                children.append(
                    _build(child, child_label, ancestors | {child}, depth + 1)
                )
        return {
            "uid":      next(counter),
            "shape_id": shape_id,
            "label":    label,
            "children": children,
        }

    outline: list[dict] = []
    for child, child_label in _child_refs(by_shape.get(MARVA_ROOT_SHAPE, [])):
        outline.append(
            _build(child, child_label, frozenset({MARVA_ROOT_SHAPE, child}), 1)
        )
    return outline


# ── public, source-aware API ──────────────────────────────────────────────────

async def fetch_templates(source: str) -> list[dict]:
    """Return one ``{filename, type}`` entry per template in ``source``.

    ``source`` is either a bf-interop release tag or :data:`MARVA_SOURCE`.
    """
    if source in _template_cache:
        return _template_cache[source]
    if source == MARVA_SOURCE:
        result = _marva_templates(await _fetch_csv())
    else:
        result = await _bf_interop_templates(source)
    _template_cache[source] = result
    return result


async def fetch_tsv_content(source: str, filename: str) -> str | None:
    """Return the DCTAP rows for ``filename`` in ``source`` as TSV text.

    For bf-interop, ``filename`` is a TSV file name within the release zip.
    For marva, ``filename`` is a ``shapeID``.  Returns ``None`` if not found.
    """
    if source == MARVA_SOURCE:
        return _marva_shape_tsv(await _fetch_csv(), filename)
    return await _bf_interop_content(source, filename)


async def fetch_marva_outline() -> list[dict]:
    """Return the marva-profiles starting-point outline (see :func:`_marva_outline`)."""
    return _marva_outline(await _fetch_csv())
