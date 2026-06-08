"""
PropCardFactory and its supporting helpers for the Sinopia BIBFRAME editor.

Builds prop-card / input-card HTML from SHACL shapes or raw RDF properties.
Extracted from ``main.py``; ``main.py`` imports the public names back from here.
"""
import rdflib

from editor_state import EditorState

try:
    from jinja2 import Template
except ImportError:
    Template = None

# JSON-LD predicates that are display metadata only — not editable content.
DISPLAY_SKIP = frozenset({"label"})

# ── Jinja2 Templates ───────────────────────────────────────────────────────────

TEMPLATE_PROP_CARD = Template("""<div class="prop-card"
     id="{{ card_id }}"{% if shape_uri %} data-rdf-shape="{{ shape_uri }}"{% endif %}{% if target_class %} data-rdf-class="{{ target_class }}"{% endif %}
     data-rdf-subject="{{ resource_uri }}">
  <div class="d-flex justify-content-between align-items-start mb-3">
    <strong class="small">{{ title }}</strong>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {{ content }}
</div>""") if Template else None

TEMPLATE_LITERAL_INPUT = Template("""<div class="input-card mb-3"
     id="{{ input_id }}"
     data-rdf-path="{{ path }}">
  <div class="d-flex justify-content-between align-items-center mb-1">
    <span class="small fw-semibold">{{ name }} {{ star }}{{ badge }}</span>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {{ meta }}
  <div class="small text-muted mb-2">{{ prompt }}</div>
  <div class="d-flex align-items-start gap-2">
    <textarea class="form-control form-control-sm" rows="2"
              id="{{ textarea_id }}"
              data-field="{{ textarea_id }}"
              data-rdf-path="{{ path }}">{{ value }}</textarea>
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
  <div class="mt-1"><a href="#" class="add-link text-primary text-decoration-none d-block mb-1">+ Add {{ name }}</a></div>
</div>""") if Template else None

TEMPLATE_URI_INPUT = Template("""<div class="input-card mb-3"
     id="{{ input_id }}"
     data-rdf-path="{{ path }}">
  <div class="d-flex justify-content-between align-items-center mb-1">
    <span class="small fw-semibold">{{ name }} {{ star }}{{ badge }}</span>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {{ meta }}
  <div class="d-flex align-items-start gap-2 mb-2">
    <span class="small text-muted pt-1 me-1" style="min-width:2.5rem;">URI</span>
    <textarea class="form-control form-control-sm" rows="2"
              id="{{ uri_id }}"
              data-field="{{ uri_id }}"
              data-rdf-path="{{ path }}">{{ uri_value }}</textarea>
    <div class="d-flex flex-column align-items-center gap-1 flex-shrink-0">
      {% if uri_value %}<a href="{{ uri_value }}" target="_blank" rel="noopener" class="btn btn-link btn-sm p-0"><i class="bi bi-box-arrow-up-right"></i></a>{% endif %}
      <button class="btn btn-link btn-sm p-0 text-secondary">
        <i class="bi bi-trash icon-btn"></i>
      </button>
    </div>
  </div>
  <div class="d-flex align-items-start gap-2">
    <span class="small text-muted pt-1 me-1" style="min-width:2.5rem;">Label</span>
    <textarea class="form-control form-control-sm" rows="2"
              id="{{ label_id }}"
              data-field="{{ label_id }}"
              data-rdf-path="{{ rdfs_label }}">{{ label_value }}</textarea>
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
  <div class="mt-1"><a href="#" class="add-link text-primary text-decoration-none d-block mb-1">+ Add {{ name }}</a></div>
</div>""") if Template else None


# ── SHACL PropertyShape descriptor ──────────────────────────────────────────────

class PropShape(object):
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


# ── DOM / HTML helpers ──────────────────────────────────────────────────────────

def sid(label: str) -> str:
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


def _add_link(label: str, external: bool = False) -> str:
    """Return an '+ Add …' anchor, optionally with an external-link icon."""
    ext = ' <i class="bi bi-box-arrow-up-right" style="font-size:.7rem;"></i>' if external else ""
    return (
        f'<a href="#" class="add-link text-primary text-decoration-none d-block mb-1">'
        f'+ Add {label}{ext}</a>'
    )


def values_for_path(state: "EditorState", path: str) -> list:
    """Return leaf values for a predicate URI from the editor state.

    Matches full URI keys, URI-suffixed keys, and compacted (short) keys.
    Blank nodes are filtered out at parse time in EditorState._extract_props().
    """
    if path in state.props:
        return list(state.props[path])

    frag = path.split("/")[-1].split("#")[-1]
    for pred, vals in state.props.items():
        if pred.endswith("/" + frag) or pred.endswith("#" + frag) or pred == frag:
            return list(vals)
    return []


_SEVERITY_BADGE = {
    "violation": '<span class="badge bg-danger ms-1">Violation</span>',
    "warning":   '<span class="badge bg-warning text-dark ms-1">Warning</span>',
    "info":      '<span class="badge bg-info text-dark ms-1">Info</span>',
}


# ── PropCardFactory ─────────────────────────────────────────────────────────────

class PropCardFactory(object):
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
        # js.console(f"In prop_card factory, state is {self._state}")

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
        card_id      = f"propcard-{sid(shape_name)}"
        viol_by_path = {v["path"]: v["severity"] for v in violations}

        input_cards = []
        has_required = False
        for ps in prop_shapes:
            if ps.required:
                has_required = True
            values   = values_for_path(self._state, ps.path)
            severity = viol_by_path.get(ps.path, "")
            if not ps.required and not values and not severity:
                continue
            input_cards.extend(self._property_input_cards(ps, values, severity))

        # Show card if it has content or if shape has required properties
        if not input_cards and not has_required:
            return ""

        if TEMPLATE_PROP_CARD:
            return TEMPLATE_PROP_CARD.render(
                card_id=card_id,
                shape_uri=shape_uri or "",
                target_class=target_class or "",
                resource_uri=self._state.resource_uri,
                title=shape_name,
                content="".join(input_cards)
            )

        # Fallback for when jinja2 is not available
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

    @staticmethod
    def _is_uri(value: str, ps: PropShape) -> bool:
        """True when the value should be displayed as a URI reference."""
        return bool(ps.value_class) or value.startswith(("http://", "https://"))

    def _input_card_html(
        self, ps: PropShape, idx: int, value: str, severity: str = ""
    ) -> str:
        """Dispatch to URI widget or literal widget based on the value type."""
        if self._is_uri(value, ps):
            return self._uri_input_card_html(ps, idx, value, severity)
        return self._literal_input_card_html(ps, idx, value, severity)

    def _card_header(self, ps: PropShape, idx: int, severity: str) -> str:
        star  = '<span class="text-danger">*</span>' if ps.required and idx == 0 else ""
        badge = _SEVERITY_BADGE.get(severity, "")
        meta  = (
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
        return star, badge, meta

    def _uri_input_card_html(
        self, ps: PropShape, idx: int, uri_value: str, severity: str = ""
    ) -> str:
        """URI + Label two-row widget for class-valued or URI-valued properties."""
        input_id  = f"inputcard-{sid(ps.name)}-{idx}"
        uri_id    = f"{input_id}-uri"
        label_id  = f"{input_id}-label"
        star, badge, meta = self._card_header(ps, idx, severity)
        safe_uri  = uri_value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        label_val = self._state.labels.get(uri_value, "")
        safe_label = label_val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if TEMPLATE_URI_INPUT:
            return TEMPLATE_URI_INPUT.render(
                input_id=input_id, uri_id=uri_id, label_id=label_id,
                path=ps.path, name=ps.name, star=star, badge=badge, meta=meta,
                uri_value=safe_uri, label_value=safe_label, rdfs_label=f"{rdflib.RDFS}label"
            )

        # Fallback for when jinja2 is not available
        ext_link = ""
        if uri_value:
            ext_link = (
                f'<a href="{safe_uri}" target="_blank" rel="noopener"'
                f' class="btn btn-link btn-sm p-0">'
                f'<i class="bi bi-box-arrow-up-right"></i></a>'
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
  <div class="d-flex align-items-start gap-2 mb-2">
    <span class="small text-muted pt-1 me-1" style="min-width:2.5rem;">URI</span>
    <textarea class="form-control form-control-sm" rows="2"
              id="{uri_id}"
              data-field="{uri_id}"
              data-rdf-path="{ps.path}">{safe_uri}</textarea>
    <div class="d-flex flex-column align-items-center gap-1 flex-shrink-0">
      {ext_link}
      <button class="btn btn-link btn-sm p-0 text-secondary">
        <i class="bi bi-trash icon-btn"></i>
      </button>
    </div>
  </div>
  <div class="d-flex align-items-start gap-2">
    <span class="small text-muted pt-1 me-1" style="min-width:2.5rem;">Label</span>
    <textarea class="form-control form-control-sm" rows="2"
              id="{label_id}"
              data-field="{label_id}"
              data-rdf-path="{rdflib.RDFS}label">{safe_label}</textarea>
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

    def _literal_input_card_html(
        self, ps: PropShape, idx: int, value: str, severity: str = ""
    ) -> str:
        """Single-textarea widget for literal-valued properties."""
        input_id    = f"inputcard-{sid(ps.name)}-{idx}"
        textarea_id = f"{input_id}-value"
        star, badge, meta = self._card_header(ps, idx, severity)
        safe_val    = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        prompt      = ps.description or "Enter a literal"

        if TEMPLATE_LITERAL_INPUT:
            return TEMPLATE_LITERAL_INPUT.render(
                input_id=input_id, textarea_id=textarea_id, path=ps.path,
                name=ps.name, star=star, badge=badge, meta=meta,
                prompt=prompt, value=safe_val
            )

        # Fallback for when jinja2 is not available
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
        Blank nodes are filtered out at parse time in EditorState._extract_props().
        """
        card_id = f"propcard-{sid(self._state.type_short())}"
        shown   = set(DISPLAY_SKIP)
        inputs  = []

        for pred, vals in self._state.props.items():
            if not vals:
                continue
            frag = pred.split("/")[-1].split("#")[-1]
            if frag in shown:
                continue
            shown.add(frag)
            ps = PropShape(
                path=pred, name=frag, required=False,
                value_class="", datatype="", description="", order=999,
            )
            inputs.extend(self._property_input_cards(ps, vals))

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

    def build_fallback_card_excluding(self, excluded_paths: set) -> str:
        """Build a prop-card for unhandled properties not in excluded_paths.

        Used in SHACL mode to show properties not covered by loaded shapes.
        Returns an empty string when there are no unhandled displayable properties.
        Blank nodes are filtered out at parse time in EditorState._extract_props().
        """
        def _is_excluded(pred: str) -> bool:
            """Check if pred matches any excluded path (handles URI variations)."""
            if pred in excluded_paths:
                return True
            pred_frag = pred.split("/")[-1].split("#")[-1]
            for excl_path in excluded_paths:
                excl_frag = excl_path.split("/")[-1].split("#")[-1]
                if excl_frag == pred_frag:
                    return True
            return False

        card_id = f"propcard-unhandled"
        shown   = set(DISPLAY_SKIP)
        inputs  = []

        for pred, vals in self._state.props.items():
            if _is_excluded(pred):
                continue
            if not vals:
                continue
            frag = pred.split("/")[-1].split("#")[-1]
            if frag in shown:
                continue
            shown.add(frag)
            ps = PropShape(
                path=pred, name=frag, required=False,
                value_class="", datatype="", description="", order=999,
            )
            inputs.extend(self._property_input_cards(ps, vals))

        if not inputs:
            return ""

        if TEMPLATE_PROP_CARD:
            return TEMPLATE_PROP_CARD.render(
                card_id=card_id,
                shape_uri="",
                target_class="",
                resource_uri=self._state.resource_uri,
                title="Other Properties",
                content="".join(inputs)
            )

        return f"""
<div class="prop-card"
     id="{card_id}"
     data-rdf-subject="{self._state.resource_uri}">
  <div class="d-flex justify-content-between align-items-start mb-3">
    <strong class="small">Other Properties</strong>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {"".join(inputs)}
</div>"""