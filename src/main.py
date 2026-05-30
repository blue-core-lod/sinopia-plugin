"""
PyScript module for the Sinopia BIBFRAME editor.
Loaded in the browser via <script type="py" src="./src/main.py">.

Guard: the entry-point coroutine is only scheduled when running inside
Pyodide/PyScript (sys.platform == 'emscripten').  This lets the module
be imported normally by the test suite without touching any browser API.
"""
import sys as _sys

from pyscript import document, when
from pyodide.http import pyfetch

# ── Constants ──────────────────────────────────────────────────────────────────

BF   = "http://id.loc.gov/ontologies/bibframe/"
BFLC = "http://id.loc.gov/ontologies/bflc/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"

# BIBFRAME Work property navigation list
# (display_label, uri_fragment, required)
WORK_NAV = [
    ("Work Title",                                          "title",                True),
    ("Variant and/or Parallel Work Title",                  "title",                False),
    ("Primary Contribution",                                "contribution",         True),
    ("Other creator(s)/contributor(s)",                     "contribution",         False),
    ("Government Publication Type",                         "govtPublicationType",  False),
    ("Date/Legal Date of Work",                             "originDate",           False),
    ("Place of Origin of the Work",                         "originPlace",          False),
    ("Language",                                            "language",             True),
    ("Script",                                              "script",               False),
    ("Language of Accompanying Work",                       "accompaniedBy",        False),
    ("Geographic Coverage of the Content of the Resource",  "geographicCoverage",   False),
    ("Time Coverage of the Content of the Resource",        "temporalCoverage",     False),
    ("Intended Audience",                                   "intendedAudience",     False),
    ("Dissertation or Thesis Information",                  "dissertation",         False),
    ("Illustrative Content",                                "illustrativeContent",  False),
    ("Color Content",                                       "colorContent",         False),
    ("Note about the Work",                                 "note",                 False),
]


# ── State ──────────────────────────────────────────────────────────────────────

class EditorState:
    """All client-side editor state for a single resource."""

    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self.resource_uri = ""
        self.resource_types: list = []
        self.resource_label = ""
        self.raw_data: dict = {}
        self.triples: list = []      # [(subject, predicate, object_str)]
        self.props: dict = {}        # predicate_uri -> [value_str, ...]
        self.field_edits: dict = {}  # field_id -> current value
        self.expanded_sections: set = {"Work Title"}

    async def load(self) -> None:
        """Fetch JSON-LD from the BFF proxy and parse it."""
        response = await pyfetch(
            f"/sinopia/api/resource/{self.resource_id}",
            headers={"Accept": "application/ld+json, application/json;q=0.9"},
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status}: {response.status_text}")
        data = await response.json()
        self.raw_data = data
        self._parse(data)

    def _parse(self, data) -> None:
        """Parse a JSON-LD object (compacted or expanded) into internal state."""
        # Expanded JSON-LD is a list; pick the first HTTP-URI node
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and str(item.get("@id", "")).startswith("http"):
                    data = item
                    break
            else:
                data = data[0] if data else {}

        self.resource_uri = data.get(
            "@id", f"https://dev.bcld.info/works/{self.resource_id}"
        )

        types = data.get("@type", [])
        self.resource_types = [types] if isinstance(types, str) else list(types)

        for key in [f"{RDFS}label", "rdfs:label"]:
            if key in data:
                self.resource_label = self._literal(data[key])
                break

        skip = {"@id", "@type", "@context"}
        subject = self.resource_uri
        for pred, raw_val in data.items():
            if pred in skip:
                continue
            values = raw_val if isinstance(raw_val, list) else [raw_val]
            for v in values:
                obj_str = self._literal(v)
                self.triples.append((subject, pred, obj_str))
                self.props.setdefault(pred, []).append(obj_str)

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

    def main_title(self) -> str:
        """Return the first bf:mainTitle value, or empty string."""
        for pred, vals in self.props.items():
            if "mainTitle" in pred:
                return vals[0] if vals else ""
        return ""

    def has_prop(self, frag: str) -> bool:
        """True if any predicate URI contains frag."""
        return any(frag in p for p in self.props)


# ── DOM-id helpers ─────────────────────────────────────────────────────────────

def _sid(label: str) -> str:
    """Derive a stable, URL-safe DOM id from a human-readable label."""
    return (
        label.lower()
             .replace(" ", "-")
             .replace("/", "")
             .replace("(", "")
             .replace(")", "")
             .replace(",", "")
             .replace("'", "")
    )


# ── HTML builders ──────────────────────────────────────────────────────────────

def _section(title: str, required: bool, body: str) -> str:
    """Wrap body HTML in a labelled property-section container."""
    sid  = _sid(title)
    star = '<span class="text-danger">*</span>' if required else ""
    return f"""
<div class="property-section" id="section-{sid}">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <span class="property-section-title">
      <i class="bi bi-chevron-down me-1" style="font-size:.7rem;"></i>
      {title} {star}
      <button class="btn btn-link btn-sm p-0 ms-1">
        <i class="bi bi-info-circle-fill text-primary icon-btn"></i>
      </button>
      <button class="btn btn-link btn-sm p-0">
        <i class="bi bi-box-arrow-up-right text-primary icon-btn"></i>
      </button>
    </span>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {body}
</div>"""


def _add_link(label: str, external: bool = False) -> str:
    """Return an '+ Add …' anchor, optionally with an external-link icon."""
    ext = ' <i class="bi bi-box-arrow-up-right" style="font-size:.7rem;"></i>' if external else ""
    return (
        f'<a href="#" class="add-link text-primary text-decoration-none d-block mb-1">'
        f'+ Add {label}{ext}</a>'
    )


def _input_card(prop_uri: str, prompt: str, input_id: str, value: str = "") -> str:
    """Return an editable literal-value card with a textarea."""
    safe_val = (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""
<div class="input-card">
  <div class="small text-muted mb-1">Property: {prop_uri}</div>
  <div class="small text-muted mb-2">{prompt}</div>
  <div class="d-flex align-items-start gap-2">
    <textarea class="form-control form-control-sm" rows="2"
              id="{input_id}"
              data-field="{input_id}">{safe_val}</textarea>
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
</div>"""


# ── Section builders ───────────────────────────────────────────────────────────

def _build_work_title(state: EditorState) -> str:
    """Build the Work Title property section, pre-filling the mainTitle value."""
    val = state.main_title()
    preferred = f"""
<div class="d-flex justify-content-between align-items-center mb-1">
  <span class="small fw-semibold">
    <i class="bi bi-chevron-down" style="font-size:.7rem;"></i>
    Preferred Title for Work <span class="text-danger">*</span>
    <button class="btn btn-link btn-sm p-0 ms-1">
      <i class="bi bi-info-circle-fill text-primary icon-btn"></i>
    </button>
    <button class="btn btn-link btn-sm p-0">
      <i class="bi bi-box-arrow-up-right text-primary icon-btn"></i>
    </button>
  </span>
  <button class="btn btn-link btn-sm p-0 text-secondary">
    <i class="bi bi-trash icon-btn"></i>
  </button>
</div>
{_input_card(
    "main title (http://id.loc.gov/ontologies/bibframe/mainTitle)",
    "Enter a literal",
    "input-main-title",
    val,
)}
<div class="mb-2">{_add_link("another Preferred Title for Work")}</div>
<div class="d-flex justify-content-between align-items-center mb-1 mt-1">
  <span class="small fw-semibold">
    <i class="bi bi-chevron-down" style="font-size:.7rem;"></i>
    Number of non-filing characters <span class="text-danger">*</span>
  </span>
  <button class="btn btn-link btn-sm p-0 text-secondary">
    <i class="bi bi-trash icon-btn"></i>
  </button>
</div>
{_input_card(
    "Non-sort character count (http://id.loc.gov/ontologies/bflc/nonSortNum)",
    "Enter an integer",
    "input-non-sort-num",
)}
<div class="mt-1">
  {_add_link("Part Number/Letter", external=True)}
  {_add_link("Preferred Title for Part", external=True)}
  {_add_link("Note about the Work Title")}
</div>"""

    card = f"""
<div class="prop-card">
  <div class="d-flex justify-content-between align-items-start mb-2">
    <strong class="small">Title--Work Title</strong>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  <div class="small text-muted mb-1">Property: Title (http://id.loc.gov/ontologies/bibframe/title)</div>
  <div class="small text-muted mb-2">Class: http://id.loc.gov/ontologies/bibframe/Title</div>
  {preferred}
</div>"""
    return _section("Work Title", True, card)


def _build_variant_title() -> str:
    """Build the Variant and/or Parallel Work Title section."""
    parallel_card = f"""
<div class="prop-card">
  <div class="d-flex justify-content-between align-items-start mb-2">
    <strong class="small">Title--Parallel Title</strong>
    <a href="#" class="add-link text-primary text-decoration-none">+ Add another Title--Parallel Title</a>
  </div>
  <div class="small text-muted mb-1">Property: Title (http://id.loc.gov/ontologies/bibframe/title)</div>
  <div class="small text-muted mb-2">Class: http://id.loc.gov/ontologies/bibframe/ParallelTitle</div>
  {_add_link("Parallel Title")}
  {_add_link("Number of Non-Filing Characters")}
  {_add_link("Parallel Other Title Information")}
  {_add_link("Parallel Part Number/Letter", external=True)}
  {_add_link("Parallel Part Title", external=True)}
  {_add_link("Date")}
  {_add_link("Note on Parallel Title")}
</div>"""

    variant_card = f"""
<div class="prop-card">
  <div class="d-flex justify-content-between align-items-start mb-2">
    <strong class="small">Title--Work Title Variant</strong>
    <a href="#" class="add-link text-primary text-decoration-none">+ Add another Title--Work Title Variant</a>
  </div>
  <div class="small text-muted mb-1">Property: Title (http://id.loc.gov/ontologies/bibframe/title)</div>
  <div class="small text-muted mb-2">Class: http://id.loc.gov/ontologies/bibframe/VariantTitle</div>
  <div class="d-flex justify-content-between align-items-center mb-1">
    <span class="small fw-semibold">
      <i class="bi bi-chevron-down" style="font-size:.7rem;"></i>
      Variant Title for Work
      <i class="bi bi-box-arrow-up-right text-primary icon-btn"></i>
    </span>
    <button class="btn btn-link btn-sm p-0 text-secondary">
      <i class="bi bi-trash icon-btn"></i>
    </button>
  </div>
  {_input_card(
      "main title (http://id.loc.gov/ontologies/bibframe/mainTitle)",
      "Enter a literal",
      "input-variant-title",
  )}
</div>"""

    return _section(
        "Variant and/or Parallel Work Title", False, parallel_card + "\n" + variant_card
    )


def _build_simple(label: str, required: bool) -> str:
    """Build a minimal property section containing only an '+ Add' link."""
    body = f'<div class="prop-card">{_add_link(label)}</div>'
    return _section(label, required, body)


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
        import js
        js.navigator.clipboard.writeText(state.resource_uri)


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
    parts = []
    for label, frag, required in WORK_NAV:
        has_data = state.has_prop(frag)
        arrow    = "&#x276F; " if has_data else "&nbsp;&nbsp; "
        bullet   = " &bull;" if has_data and required else ""
        cls      = "has-data" if has_data else "no-data"
        sid      = _sid(label)
        parts.append(
            f'<div class="left-nav-item {cls}">'
            f'<a href="#section-{sid}" '
            f"onclick=\"document.getElementById('section-{sid}')"
            f"?.scrollIntoView({{behavior:'smooth'}});return false;\">"
            f'<span style="font-size:.7rem;">{arrow}</span>{label}{bullet}'
            f'</a></div>'
        )
    document.getElementById("left-nav").innerHTML = "\n".join(parts)


def render_main_editor(state: EditorState) -> None:
    sections = [
        _build_work_title(state),
        _build_variant_title(),
        _section("Primary Contribution", True,
                 f'<div class="prop-card">{_add_link("Primary Contribution")}</div>'),
        _section("Other creator(s)/contributor(s)", False,
                 f'<div class="prop-card">{_add_link("Contributor")}</div>'),
        _build_simple("Government Publication Type", False),
        _build_simple("Date/Legal Date of Work", False),
        _build_simple("Place of Origin of the Work", False),
        _section("Language", True,
                 f'<div class="prop-card">{_add_link("Language")}</div>'),
        _build_simple("Script", False),
        _build_simple("Language of Accompanying Work", False),
        _build_simple("Geographic Coverage of the Content of the Resource", False),
        _build_simple("Time Coverage of the Content of the Resource", False),
        _build_simple("Intended Audience", False),
        _build_simple("Dissertation or Thesis Information", False),
        _build_simple("Illustrative Content", False),
        _build_simple("Color Content", False),
        _build_simple("Note about the Work", False),
    ]
    document.getElementById("main-editor").innerHTML = "\n".join(sections)

    for ta in document.querySelectorAll("textarea[data-field]"):
        fid = ta.getAttribute("data-field")
        state.field_edits[fid] = ta.value

        @when("input", f"#{fid}")
        def _track(evt, _fid=fid):
            state.field_edits[_fid] = evt.target.value


def render_tabs() -> None:
    """Wire the Navigation / Versions / Relationships tab buttons."""
    tab_map     = {
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
