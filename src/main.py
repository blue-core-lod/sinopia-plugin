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

# ── Constants ──────────────────────────────────────────────────────────────────

BF   = "http://id.loc.gov/ontologies/bibframe/"
BFLC = "http://id.loc.gov/ontologies/bflc/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SH   = rdflib.Namespace("http://www.w3.org/ns/shacl#")

WORK_NAV = []  # SHACL shapes drive the editor; kept for test compatibility.

# JSON-LD predicates that are display metadata only — not editable content.
_DISPLAY_SKIP = frozenset({"label"})


# ── State ──────────────────────────────────────────────────────────────────────

class EditorState:
    """All client-side editor state for a single resource."""

    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self.resource_uri = ""
        self.resource_types: list = []
        self.resource_label = ""
        self.raw_data: dict = {}
        self.triples: list = []    # [(subject, predicate, object_str)]
        self.props: dict = {}      # predicate -> [value_str, ...]
        self.field_edits: dict = {}
        self.expanded_sections: set = set()

    async def load(self) -> None:
        """Fetch JSON-LD from the BFF proxy and parse it."""
        response = await pyfetch(
            f"/sinopia/api/resource/{self.resource_id}?expand=true",
            headers={"Accept": "application/ld+json, application/json;q=0.9",
                     "User-Agent": "Sinopia"},
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status}: {response.status_text}")
        data = await response.json()
        if isinstance(data, dict):
            self.resource_uri = data.get("@id", "")
        self.raw_data = data
        self._parse(data)

    def _parse(self, data) -> None:
        """Parse a JSON-LD object (compacted or expanded) into internal state."""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and str(item.get("@id", "")).startswith("http"):
                    data = item
                    break
            else:
                data = data[0] if data else {}

        if not self.resource_uri:
            self.resource_uri = data.get(
                "@id", f"https://dev.bcld.info/works/{self.resource_id}"
            )

        types = data.get("@type", [])
        self.resource_types = [types] if isinstance(types, str) else list(types)

        for key in [f"{RDFS}label", "rdfs:label"]:
            if key in data:
                self.resource_label = self._literal(data[key])
                break

        self._extract_props(data, self.resource_uri, {"@id", "@type", "@context"})

    def _extract_props(self, node: dict, subject: str, skip: set) -> None:
        """Walk a JSON-LD node, storing triples and recursing into blank nodes."""
        for pred, raw_val in node.items():
            if pred in skip or pred.startswith("@"):
                continue
            values = raw_val if isinstance(raw_val, list) else [raw_val]
            for v in values:
                obj_str = self._literal(v)
                self.triples.append((subject, pred, obj_str))
                self.props.setdefault(pred, []).append(obj_str)
                # Recurse into blank nodes (no @value, no real HTTP @id)
                if isinstance(v, dict) and "@value" not in v:
                    node_id = v.get("@id", "")
                    if not node_id or node_id.startswith("_:"):
                        self._extract_props(v, subject, skip)

    @staticmethod
    def _literal(val) -> str:
        """Extract a string value from a JSON-LD value node or plain string."""
        if isinstance(val, dict):
            return str(val.get("@value", val.get("@id", val)))
        return str(val)

    def type_short(self) -> str:
        """Return the most specific type name (non-Work class, or 'Work')."""
        for t in self.resource_types:
            name = str(t).split("/")[-1].split("#")[-1]
            if name.lower() != "work":
                return name
        return "Work"

    def resource_name(self) -> str:
        return f"_Work ({self.type_short()})"

    def has_prop(self, frag: str) -> bool:
        """True if any predicate key contains frag."""
        return any(frag in p for p in self.props)


# ── SHACL helpers ──────────────────────────────────────────────────────────────

class PropShape:
    """Descriptor for a single sh:PropertyShape extracted from a SHACL graph."""
    __slots__ = ("path", "name", "required", "value_class", "datatype", "description", "order")

    def __init__(self, path, name, required, value_class, datatype, description, order):
        self.path        = path
        self.name        = name or path.split("/")[-1].split("#")[-1]
        self.required    = required
        self.value_class = value_class
        self.datatype    = datatype
        self.description = description
        self.order       = order


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


def _values_for_path(state: "EditorState", path: str) -> list:
    """Return leaf values for a predicate URI from the editor state.

    Matches full URI keys, URI-suffixed keys, and compacted (short) keys.
    Excludes stringified blank-node objects (values that start with '{' or '[').
    """
    if path in state.props:
        raw = state.props[path]
    else:
        frag = path.split("/")[-1].split("#")[-1]
        raw = []
        for pred, vals in state.props.items():
            if pred.endswith("/" + frag) or pred.endswith("#" + frag) or pred == frag:
                raw = vals
                break
    return [v for v in raw if not (v.startswith("{") or v.startswith("["))]


# ── DOM helpers ────────────────────────────────────────────────────────────────

def _sid(label: str) -> str:
    """Derive a stable, URL-safe DOM id fragment from a human-readable label or URI."""
    return (
        label.lower()
             .replace(" ", "-")
             .replace(":", "-")
             .replace("/", "-")
             .replace("#", "-")
             .replace("(", "")
             .replace(")", "")
             .replace(",", "")
             .replace("'", "")
             .strip("-")
    )


# ── HTML primitives ────────────────────────────────────────────────────────────

def _add_link(label: str, external: bool = False) -> str:
    """Return an '+ Add …' anchor, optionally with an external-link icon."""
    ext = ' <i class="bi bi-box-arrow-up-right" style="font-size:.7rem;"></i>' if external else ""
    return (
        f'<a href="#" class="add-link text-primary text-decoration-none d-block mb-1">'
        f'+ Add {label}{ext}</a>'
    )


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


# ── PropCardFactory ────────────────────────────────────────────────────────────

_SEVERITY_BADGE = {
    "violation": '<span class="badge bg-danger ms-1">Violation</span>',
    "warning":   '<span class="badge bg-warning text-dark ms-1">Warning</span>',
    "info":      '<span class="badge bg-info text-dark ms-1">Info</span>',
}


class PropCardFactory:
    """Builds prop-card HTML from SHACL shapes or raw RDF properties.

    Hierarchy
    ---------
    sh:NodeShape   → ``div.prop-card``   id="propcard-{class_fragment}"
                                         data-rdf-class="{targetClass_uri}"
                                         data-rdf-subject="{resource_uri}"

    sh:PropertyShape → ``div.input-card`` id="inputcard-{property_name}-{index}"
                                          data-rdf-path="{sh:path_uri}"
      (one input-card per value; one blank input-card when required and empty)
    """

    def __init__(self, state: EditorState):
        self._state = state

    # ── NodeShape → prop-card ──────────────────────────────────────────────────

    def build_node_card(
        self,
        shape_name: str,
        prop_shapes: list,
        violations: list = (),
        *,
        shape_uri: str = "",
        target_class: str = "",
    ) -> str:
        """Build a prop-card for a sh:NodeShape.

        Parameters
        ----------
        shape_name:   display label (from rdfs:label or sh:name)
        prop_shapes:  PropertyShape descriptors for this NodeShape
        violations:   SHACL validation results [{path, severity, message}]
        shape_uri:    the NodeShape's own URI  → data-rdf-shape
        target_class: sh:targetClass value     → data-rdf-class

        Required PropertyShapes are always shown.
        Optional PropertyShapes are shown when they have RDF values **or** when
        a SHACL violation / warning / info targets their path.
        Returns an empty string when there is nothing to display.
        """
        card_id      = f"propcard-{_sid(shape_name)}"
        viol_by_path = {v["path"]: v["severity"] for v in violations}

        input_cards = []
        for ps in prop_shapes:
            values   = _values_for_path(self._state, ps.path)
            severity = viol_by_path.get(ps.path, "")
            if not ps.required and not values and not severity:
                continue
            input_cards.extend(self._property_input_cards(ps, values, severity))

        if not input_cards:
            return ""

        shape_attr = f'\n     data-rdf-shape="{shape_uri}"'  if shape_uri    else ""
        class_attr = f'\n     data-rdf-class="{target_class}"' if target_class else ""

        return f"""
<div class="prop-card"
     id="{card_id}"{shape_attr}{class_attr}
     data-rdf-subject="{self._state.resource_uri}">
  <div class="d-flex justify-content-between align-items-start mb-3">
    <strong class="small">{shape_name}</strong>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {"".join(input_cards)}
</div>"""

    # ── PropertyShape → input-card(s) ─────────────────────────────────────────

    def _property_input_cards(
        self, ps: PropShape, values: list, severity: str = ""
    ) -> list:
        """Return one input-card per RDF value (or one blank card if none)."""
        if values:
            return [
                self._input_card_html(ps, i, v, severity if i == 0 else "")
                for i, v in enumerate(values)
            ]
        # Required or violation with no value → blank card for user to fill.
        return [self._input_card_html(ps, 0, "", severity)]

    def _input_card_html(
        self, ps: PropShape, idx: int, value: str, severity: str = ""
    ) -> str:
        """Build a single ``div.input-card`` with stable RDF-resolvable IDs."""
        input_id  = f"inputcard-{_sid(ps.name)}-{idx}"
        textarea_id = f"{input_id}-value"
        safe_val  = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        frag      = ps.path.split("/")[-1].split("#")[-1]
        prompt    = ps.description or ("Enter a URI" if ps.value_class else "Enter a literal")
        star      = '<span class="text-danger">*</span>' if ps.required and idx == 0 else ""
        badge     = _SEVERITY_BADGE.get(severity, "")

        meta = (
            f'<div class="small text-muted mb-1">'
            f'Property: <span class="font-monospace">{ps.path}</span></div>'
        )
        if ps.value_class:
            meta += (
                f'<div class="small text-muted mb-1">'
                f'Class: <span class="font-monospace">{ps.value_class}</span></div>'
            )
        elif ps.datatype:
            meta += (
                f'<div class="small text-muted mb-1">'
                f'Datatype: <span class="font-monospace">{ps.datatype}</span></div>'
            )

        return f"""
<div class="input-card mb-3"
     id="{input_id}"
     data-rdf-path="{ps.path}">
  <div class="d-flex justify-content-between align-items-center mb-1">
    <span class="small fw-semibold">{ps.name} {star}{badge}</span>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {meta}
  <div class="small text-muted mb-2">{prompt}</div>
  <div class="d-flex align-items-start gap-2">
    <textarea class="form-control form-control-sm" rows="2"
              id="{textarea_id}"
              data-field="{textarea_id}"
              data-rdf-path="{ps.path}">{safe_val}</textarea>
    <div class="d-flex flex-column align-items-center gap-1 flex-shrink-0">
      <button class="btn btn-sm btn-outline-secondary" title="Diacritics">ä</button>
      <small class="text-muted text-center" style="white-space:nowrap;font-size:.72rem;">
        No language<br>specified
      </small>
      <button class="btn btn-link btn-sm p-0 text-secondary">
        <i class="bi bi-trash icon-btn"></i>
      </button>
    </div>
  </div>
  <div class="mt-1">{_add_link(ps.name)}</div>
</div>"""

    # ── Fallback: raw RDF → single generic prop-card ──────────────────────────

    def build_fallback_card(self) -> str:
        """Build a prop-card from raw RDF props when no SHACL shapes are loaded.

        One input-card is generated per predicate with leaf values.
        Returns an empty string when the resource has no displayable properties.
        """
        card_id = f"propcard-{_sid(self._state.type_short())}"
        shown   = set(_DISPLAY_SKIP)
        inputs  = []

        for pred, vals in self._state.props.items():
            frag      = pred.split("/")[-1].split("#")[-1]
            leaf_vals = [v for v in vals if not (v.startswith("{") or v.startswith("["))]
            if not leaf_vals or frag in shown:
                continue
            shown.add(frag)
            ps = PropShape(
                path=pred, name=frag, required=False,
                value_class="", datatype="", description="", order=999,
            )
            inputs.extend(self._property_input_cards(ps, leaf_vals))

        if not inputs:
            return ""

        return f"""
<div class="prop-card"
     id="{card_id}"
     data-rdf-subject="{self._state.resource_uri}">
  <div class="d-flex justify-content-between align-items-start mb-3">
    <strong class="small">{self._state.resource_name()}</strong>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {"".join(inputs)}
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
            card_id   = f"propcard-{_sid(name)}"
            prop_list = _prop_shapes(shacl, shape)
            has_data  = any(_values_for_path(state, ps.path) for ps in prop_list)
            has_req   = any(ps.required for ps in prop_list)
            if not has_data and not has_req:
                continue
            parts.append(_nav_item(name, card_id, has_data))
    else:
        # Fallback: data-driven, linking into the single generic prop-card.
        parts = []
        shown = set(_DISPLAY_SKIP)
        for pred, vals in state.props.items():
            frag      = pred.split("/")[-1].split("#")[-1]
            leaf_vals = [v for v in vals if not (v.startswith("{") or v.startswith("["))]
            if not leaf_vals or frag in shown:
                continue
            shown.add(frag)
            target_id = f"inputcard-{_sid(frag)}-0"
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

    Fallback mode
    -------------
    A single generic ``div.prop-card`` holding one ``div.input-card`` per
    predicate that has leaf values in the loaded RDF.
    """
    factory   = PropCardFactory(state)
    shacl     = _load_shacl_graph()
    all_nodes = _all_shapes(shacl)

    if all_nodes:
        # SHACL mode: one prop-card per NodeShape — all shapes in the template.
        violations = _validate(state, shacl)
        sections   = []
        for shape in all_nodes:
            shape_name   = _shape_label(shacl, shape)
            shape_uri    = str(shape) if isinstance(shape, rdflib.URIRef) else ""
            target_class = str(shacl.value(shape, SH.targetClass) or "")
            card = factory.build_node_card(
                shape_name,
                _prop_shapes(shacl, shape),
                violations,
                shape_uri=shape_uri,
                target_class=target_class,
            )
            if card:
                sections.append(card)
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
