"""
PyScript module for the Sinopia BIBFRAME editor.
Loaded in the browser via <script type="py" src="./src/main.py">.

Guard: the entry-point coroutine is only scheduled when running inside
Pyodide/PyScript (sys.platform == 'emscripten').  This lets the module
be imported normally by the test suite without touching any browser API.
"""
import json
import sys as _sys

import rdflib
from pyscript import document, when
from pyodide.http import pyfetch
import js

from editor_state import EditorState
from factories.property_card import (
    PropCardFactory,
    PropShape,
    DISPLAY_SKIP,
    sid,
    values_for_path,
)

# ── Constants ──────────────────────────────────────────────────────────────────

BF   = rdflib.Namespace("http://id.loc.gov/ontologies/bibframe/")
BFLC = rdflib.Namespace("http://id.loc.gov/ontologies/bflc/")
MADS = rdflib.Namespace("http://www.loc.gov/mads/rdf/v1#")
SH   = rdflib.Namespace("http://www.w3.org/ns/shacl#")

_ENHANCEMENT_NAMESPACES = [BF, BFLC, MADS]

WORK_NAV = []  # SHACL shapes drive the editor; kept for test compatibility.

# ── SHACL helpers ──────────────────────────────────────────────────────────────

def _load_shacl_graph() -> rdflib.Graph:
    """Merge all Turtle strings stored in localStorage['template'] into one Graph."""
    g = rdflib.Graph()
    try:
        raw = js.localStorage.getItem("template")
        if not raw:
            return g
        items: list = json.loads(str(raw))
        for turtle in items:
            try:
                g.parse(data=turtle, format="turtle")
            except Exception:
                pass
    except Exception:
        pass
    return g


def _enhance_shacl_with_resource_props(shacl: rdflib.Graph, state: "EditorState") -> rdflib.Graph:
    """Add missing vocabulary properties from the resource to their corresponding SHACL shapes.

    For each NodeShape, finds all properties from configured namespaces (bf, bflc, mads)
    in the resource that aren't already in the shape and adds them as optional properties.
    """
    # Get all properties from enhancement namespaces that exist in the resource
    resource_props_by_ns = {
        str(ns): {p for p in state.props if p.startswith(str(ns))}
        for ns in _ENHANCEMENT_NAMESPACES
    }

    # For each shape, add missing properties from enhancement namespaces
    for shape in _all_shapes(shacl):
        target_class = shacl.value(shape, SH.targetClass)
        if not target_class:
            continue

        # Get existing property paths in this shape
        existing_paths = {
            shacl.value(prop, SH.path)
            for prop in shacl.objects(shape, SH.property)
            if shacl.value(prop, SH.path)
        }

        # Calculate next order number
        next_order = max(
            (int(str(shacl.value(prop, SH.order)))
             for prop in shacl.objects(shape, SH.property)
             if shacl.value(prop, SH.order)),
            default=0
        ) + 1

        # Add missing properties from all enhancement namespaces
        for ns_uri, props_in_resource in resource_props_by_ns.items():
            for prop_path in sorted(props_in_resource):
                prop_uri = rdflib.URIRef(prop_path)
                if prop_uri not in existing_paths:
                    prop_shape = rdflib.BNode()
                    shacl.add((shape, SH.property, prop_shape))
                    shacl.add((prop_shape, SH.path, prop_uri))
                    # Extract property name from URI
                    prop_name = prop_path.split("/")[-1].split("#")[-1]
                    shacl.add((prop_shape, SH.name, rdflib.Literal(prop_name)))
                    shacl.add((prop_shape, SH.minCount, rdflib.Literal(0)))
                    shacl.add((prop_shape, SH.order, rdflib.Literal(next_order)))
                    next_order += 1
                    existing_paths.add(prop_uri)

    return shacl


def _shapes_for_types(shacl: rdflib.Graph, type_uris: list) -> list:
    """Return NodeShape nodes whose sh:targetClass matches any of type_uris."""
    shapes = []
    for type_uri in type_uris:
        target = rdflib.URIRef(type_uri)
        for shape in shacl.subjects(SH.targetClass, target):
            if shape not in shapes:
                shapes.append(shape)
    return shapes


def _all_shapes(shacl: rdflib.Graph) -> list:
    """Return all sh:NodeShape subjects in the SHACL graph, in a stable order.

    Named shapes (URIRef) come first sorted by URI; anonymous blank nodes follow.
    """
    named  = sorted(
        (s for s in shacl.subjects(rdflib.RDF.type, SH.NodeShape)
         if isinstance(s, rdflib.URIRef)),
        key=str,
    )
    blanks = [s for s in shacl.subjects(rdflib.RDF.type, SH.NodeShape)
              if not isinstance(s, rdflib.URIRef)]
    return named + blanks


def _shape_label(shacl: rdflib.Graph, shape_node) -> str:
    """Return the display name for a NodeShape.

    Preference: rdfs:label → sh:name → last segment of the shape URI.
    """
    for pred in (rdflib.RDFS.label, SH.name):
        val = shacl.value(shape_node, pred)
        if val is not None:
            return str(val)
    if isinstance(shape_node, rdflib.URIRef):
        raw = str(shape_node)
        # split on colon, slash, hash — take the last non-empty token
        for ch in ("#", "/", ":"):
            raw = raw.split(ch)[-1]
        return raw or str(shape_node)
    return "Shape"


def _prop_shapes(shacl: rdflib.Graph, shape_node) -> list:
    """Return sorted PropShape list from a NodeShape."""
    props = []
    for prop_bn in shacl.objects(shape_node, SH.property):
        path = shacl.value(prop_bn, SH.path)
        if path is None:
            continue
        name_node   = shacl.value(prop_bn, SH.name)
        min_count   = shacl.value(prop_bn, SH.minCount)
        value_class = shacl.value(prop_bn, SH["class"])
        datatype    = shacl.value(prop_bn, SH.datatype)
        description = shacl.value(prop_bn, SH.description)
        order_node  = shacl.value(prop_bn, SH.order)
        props.append(PropShape(
            path=str(path),
            name=str(name_node) if name_node is not None else "",
            required=int(str(min_count)) >= 1 if min_count is not None else False,
            value_class=str(value_class) if value_class is not None else "",
            datatype=str(datatype) if datatype is not None else "",
            description=str(description) if description is not None else "",
            order=int(str(order_node)) if order_node is not None else 999,
        ))
    props.sort(key=lambda p: (p.order, p.name))
    return props


def _validate(state: "EditorState", shacl: rdflib.Graph) -> list:
    """Validate the loaded resource against the SHACL graph.

    Returns a list of dicts: {path, severity, message}.
    Severity is one of 'violation', 'warning', 'info'.
    """
    try:
        import pyshacl
        data_g = rdflib.Graph()
        data_g.parse(data=json.dumps(state.raw_data), format="json-ld")
        conforms, results_g, _ = pyshacl.validate(
            data_g, shacl_graph=shacl, inference="rdfs", abort_on_first=False,
        )
        if conforms:
            return []
        out = []
        for report in results_g.subjects(rdflib.RDF.type, SH.ValidationResult):
            sev  = results_g.value(report, SH.resultSeverity)
            path = results_g.value(report, SH.resultPath)
            msg  = results_g.value(report, SH.resultMessage)
            sev_str = str(sev).split("#")[-1].lower() if sev else "violation"
            # normalise to violation / warning / info
            if "violation" in sev_str:
                sev_str = "violation"
            elif "warning" in sev_str:
                sev_str = "warning"
            else:
                sev_str = "info"
            out.append({
                "path":     str(path) if path else "",
                "severity": sev_str,
                "message":  str(msg) if msg else "",
            })
        return out
    except Exception:
        return []


# ── HTML primitives ────────────────────────────────────────────────────────────

def _input_card(prop_uri: str, prompt: str, input_id: str, value: str = "") -> str:
    """Return a bare editable textarea widget (no outer input-card div).

    Used by PropCardFactory; also kept for any direct callers and tests.
    """
    safe_val = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
<div class="d-flex align-items-start gap-2">
  <textarea class="form-control form-control-sm" rows="2"
            id="{input_id}"
            data-field="{input_id}"
            data-rdf-path="{prop_uri}">{safe_val}</textarea>
  <div class="d-flex flex-column align-items-center gap-1 flex-shrink-0">
    <button class="btn btn-sm btn-outline-secondary" title="Diacritics">ä</button>
    <small class="text-muted text-center" style="white-space:nowrap;font-size:.72rem;">
      No language<br>specified
    </small>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
</div>"""


# ── Render functions ───────────────────────────────────────────────────────────

def render_resource_header(state: EditorState) -> None:
    document.getElementById("resource-title").innerHTML = state.resource_name()
    document.getElementById("resource-badge").textContent = state.type_short().upper()
    types     = state.resource_types
    class_uri = next((t for t in types if "bibframe/Work" in t), types[0] if types else "")
    short     = class_uri.split("/")[-1] if class_uri else "Work"
    document.getElementById("resource-class").textContent = (
        f"{short} ({class_uri})" if class_uri else "—"
    )


def render_uri_bar(state: EditorState) -> None:
    document.getElementById("resource-uri").innerHTML = f"&lt;{state.resource_uri}&gt;"

    @when("click", "#copy-uri-btn")
    def _copy(_evt):
        import js as _js
        _js.navigator.clipboard.writeText(state.resource_uri)


def render_triples(state: EditorState) -> None:
    if not state.triples:
        return
    section = document.getElementById("triples-section")
    section.style.display = "block"

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = "".join(
        f"<tr>"
        f'<td style="word-break:break-all">{_esc(subj)}</td>'
        f'<td style="word-break:break-all">{_esc(pred)}</td>'
        f'<td style="word-break:break-all">{_esc(obj)}</td>'
        f"</tr>"
        for subj, pred, obj in state.triples
    )
    document.getElementById("triples-body").innerHTML = rows


def render_left_nav(state: EditorState) -> None:
    """Build the left navigation.

    In SHACL mode: one item per PropertyShape (links to its first input-card).
    In fallback mode: one item per predicate with leaf values.
    """
    shacl      = _load_shacl_graph()
    all_nodes  = _all_shapes(shacl)

    def _nav_item(label: str, target_id: str, has_data: bool, required: bool = False) -> str:
        arrow  = "&#x276F; " if has_data else "&nbsp;&nbsp; "
        bullet = " &bull;" if has_data and required else ""
        cls    = "has-data" if has_data else "no-data"
        return (
            f'<div class="left-nav-item {cls}">'
            f'<a href="#{target_id}" '
            f"onclick=\"document.getElementById('{target_id}')"
            f"?.scrollIntoView({{behavior:'smooth'}});return false;\">"
            f'<span style="font-size:.7rem;">{arrow}</span>{label}{bullet}'
            f'</a></div>'
        )

    if all_nodes:
        # SHACL mode: one nav item per NodeShape linking to its prop-card.
        parts = []
        for shape in all_nodes:
            name      = _shape_label(shacl, shape)
            card_id   = f"propcard-{sid(name)}"
            prop_list = _prop_shapes(shacl, shape)
            has_data  = any(values_for_path(state, ps.path) for ps in prop_list)
            has_req   = any(ps.required for ps in prop_list)
            if not has_data and not has_req:
                continue
            parts.append(_nav_item(name, card_id, has_data))
    else:
        # Fallback: data-driven, linking into the single generic prop-card.
        # Blank nodes are filtered out at parse time in EditorState._extract_props().
        parts = []
        shown = set(DISPLAY_SKIP)
        for pred, vals in state.props.items():
            if not vals:
                continue
            frag = pred.split("/")[-1].split("#")[-1]
            if frag in shown:
                continue
            shown.add(frag)
            target_id = f"inputcard-{sid(frag)}-0"
            parts.append(_nav_item(frag, target_id, has_data=True))

    document.getElementById("left-nav").innerHTML = "\n".join(parts)


def render_main_editor(state: EditorState) -> None:
    """Render the main editor area.

    SHACL mode
    ----------
    One ``div.prop-card`` per sh:NodeShape whose sh:targetClass matches the
    resource's RDF types.  Each sh:PropertyShape in the NodeShape becomes a
    ``div.input-card`` child.

    After building the normal cards, SHACL validation is run and any violated
    PropertyShape paths that are not yet shown are added (with a severity badge).

    Unhandled properties (not in any SHACL shape) are rendered in a fallback card.

    Fallback mode
    -------------
    A single generic ``div.prop-card`` holding one ``div.input-card`` per
    predicate that has leaf values in the loaded RDF.
    """
    factory   = PropCardFactory(state)
    shacl     = _load_shacl_graph()
    all_nodes = _all_shapes(shacl)

    sections = []
    handled_paths = set()  # Track which property paths are handled by SHACL

    if all_nodes:
        # SHACL mode: one prop-card per NodeShape — all shapes in the template.
        violations = _validate(state, shacl)
        for shape in all_nodes:
            shape_name   = _shape_label(shacl, shape)
            shape_uri    = str(shape) if isinstance(shape, rdflib.URIRef) else ""
            target_class = str(shacl.value(shape, SH.targetClass) or "")
            prop_shapes = _prop_shapes(shacl, shape)
            # Track which paths are handled by this shape
            for ps in prop_shapes:
                handled_paths.add(ps.path)
            card = factory.build_node_card(
                shape_name,
                prop_shapes,
                violations,
                shape_uri=shape_uri,
                target_class=target_class,
            )
            if card:
                sections.append(card)

        # Add fallback card for any unhandled properties
        fallback = factory.build_fallback_card_excluding(handled_paths)
        if fallback:
            sections.append(fallback)
    else:
        card = factory.build_fallback_card()
        sections = [card] if card else []

    document.getElementById("main-editor").innerHTML = "\n".join(sections)

    for ta in document.querySelectorAll("textarea[data-field]"):
        fid = ta.getAttribute("data-field")
        state.field_edits[fid] = ta.value

        @when("input", f"#{fid}")
        def _track(evt, _fid=fid):
            state.field_edits[_fid] = evt.target.value


def render_tabs() -> None:
    """Wire the Navigation / Versions / Relationships tab buttons."""
    tab_map         = {
        "tab-nav-btn": ("tab-nav-content",),
        "tab-ver-btn": ("tab-ver-content",),
        "tab-rel-btn": ("tab-rel-content",),
    }
    all_content_ids = {"tab-nav-content", "tab-ver-content", "tab-rel-content"}

    def _make_handler(btn_id: str, show_ids: set) -> None:
        @when("click", f"#{btn_id}")
        def _handler(_evt):
            for cid in all_content_ids:
                el = document.getElementById(cid)
                el.style.display = "block" if cid in show_ids else "none"
            for bid in tab_map:
                btn = document.getElementById(bid)
                if bid == btn_id:
                    btn.classList.add("active")
                else:
                    btn.classList.remove("active")

    for btn_id, show_ids in tab_map.items():
        _make_handler(btn_id, set(show_ids))


# ── Entry point ────────────────────────────────────────────────────────────────

async def _entry_point() -> None:
    """Bootstrap: fetch the resource and render the full editor UI."""
    resource_id = document.getElementById("resource-id-meta").getAttribute("content")
    state = EditorState(resource_id)
    render_tabs()

    if not resource_id:
        document.getElementById("resource-title").innerHTML = "New Resource"
        document.getElementById("resource-badge").textContent = "WORK"
        render_left_nav(state)
        render_main_editor(state)
        return

    try:
        await state.load()
        render_resource_header(state)
        render_uri_bar(state)
        render_triples(state)
        render_left_nav(state)
        render_main_editor(state)
    except Exception as exc:
        safe_id = resource_id.replace("<", "&lt;").replace(">", "&gt;")
        document.getElementById("main-editor").innerHTML = f"""
<div class="alert alert-danger">
  <strong>Error loading resource:</strong> {exc}<br>
  <small class="font-monospace">resource_id: {safe_id}</small>
</div>"""
        document.getElementById("resource-title").textContent = "Error loading resource"
        bluecore = document.getElementById("bluecore-url-meta").getAttribute("content")
        document.getElementById("resource-uri").innerHTML = (
            f"&lt;{bluecore}/works/{resource_id}&gt;"
        )


# Schedule only when running inside Pyodide/PyScript (browser environment).
if _sys.platform == "emscripten":
    import asyncio as _asyncio
    _asyncio.ensure_future(_entry_point())
