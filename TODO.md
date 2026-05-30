# TODO: Sinopia PyScript Editor — Airflow Plugin

Branch: `sinopia-pyscript-editor`

## Goal

Add an Airflow plugin route `/sinopia/editor/{resource_id}` that:
1. Fetches JSON-LD from the BLUECORE_URL environmental variable `{BLUECORE_URL/works/{resource_id}`
2. Renders a Sinopia-style BIBFRAME editor in the browser
3. Uses **PyScript** for all client-side state management

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Route host | FastAPI (existing `sinopia_plugin.py`) | Already scaffolded |
| API proxy | FastAPI + httpx | Avoid CORS; proxy `/sinopia/api/resource/{id}` → BCLD API |
| UI structure | Bootstrap 5 + Bootstrap Icons | Match Sinopia visual style |
| Client state | PyScript 2024.11.1 (Pyodide) | User requirement |
| Template | Plain HTML file + `str.replace` for `{{ resource_id }}` | No Jinja2 dep needed |

---

## Tasks

### 1. Dependencies — `pyproject.toml`
- [ ] Add `fastapi>=0.110.0`
- [ ] Add `httpx>=0.27.0`
- [ ] Add `uvicorn>=0.29.0` (dev server)

### 2. Backend — `sinopia_plugin.py`
- [ ] Add `GET /sinopia/api/resource/{resource_id}` — proxies BCLD API, returns JSON-LD
- [ ] Add `GET /sinopia/editor/{resource_id}` — serves HTML template with resource_id injected
- [ ] Keep existing `/` root endpoint

### 3. HTML template — `src/templates/editor.html`

#### 3a. Static structure (HTML/CSS)
- [ ] Blue Core header (logo, user nav stub)
- [ ] Secondary nav (Dashboard, Editor, Resource Templates, Actions)
- [ ] Resource header bar: title, type badge (WORK), clipboard + eye buttons, Close/Save
- [ ] URI display row with Copy URI button
- [ ] Collapsible triples table (Subject/Predicate/Object) — hidden until data loads
- [ ] Two-column layout: left nav (30%) + main editor (70%)
- [ ] Left nav tabs: Navigation / Versions / Relationships
- [ ] Navigation tab: class info panel + property list container
- [ ] PyScript config to suppress py-terminal

#### 3b. PyScript state (`EditorState` class)
- [ ] `resource_id`, `resource_uri`, `resource_types`, `resource_label`
- [ ] `raw_data: dict` — the full parsed JSON-LD
- [ ] `triples: list[tuple]` — all (subject, predicate, object) triples
- [ ] `props: dict` — predicate URI → list of value strings
- [ ] `expanded_sections: set` — which left-nav items are open
- [ ] `field_edits: dict` — tracks textarea/input changes
- [ ] `async load()` — fetch from `/sinopia/api/resource/{resource_id}`, parse
- [ ] `_parse(data)` — handle both expanded (list) and compacted JSON-LD formats
- [ ] Helper getters: `get_type_short()`, `get_resource_name()`, `get_main_title()`, `has_prop(frag)`

#### 3c. PyScript rendering functions
- [ ] `render_resource_header(state)` — populate title, badge
- [ ] `render_uri_bar(state)` — populate URI, wire Copy URI button
- [ ] `render_triples(state)` — show/populate triples table
- [ ] `render_left_nav(state)` — build BIBFRAME Work property list with `>` arrows + bullets
- [ ] `render_main_editor(state)` — build property editor sections

#### 3d. Property sections to render in main editor
- [ ] Work Title (required `*`) — with Preferred Title textarea pre-filled from JSON-LD, Non-filing chars field, Add links
- [ ] Variant and/or Parallel Work Title — Title--Parallel Title card, Title--Work Title Variant card with Add links
- [ ] Primary Contribution (required `*`) — Add link
- [ ] Government Publication Type — Add link
- [ ] Date/Legal Date of Work — Add link
- [ ] Place of Origin of the Work — Add link
- [ ] Language (required `*`) — Add link

#### 3e. BIBFRAME Work property nav list (left panel)
- [ ] Work Title •
- [ ] Variant and/or Parallel Work Title •
- [ ] Primary Contribution
- [ ] Other creator(s)/contributor(s)
- [ ] Government Publication Type
- [ ] Date/Legal Date of Work
- [ ] Place of Origin of the Work
- [ ] Language
- [ ] Script
- [ ] Language of Accompanying Work
- [ ] Geographic Coverage of the Content of the Resource
- [ ] Time Coverage of the Content of the Resource
- [ ] Intended Audience
- [ ] Dissertation or Thesis Information
- [ ] Illustrative Content
- [ ] Color Content
- [ ] Note about the Work

#### 3f. PyScript UI interactions
- [ ] Copy URI to clipboard
- [ ] Tab switching (Navigation / Versions / Relationships)
- [ ] Error state if fetch fails (show alert with message + raw URI)

---

## File Layout After This Branch

```
sinopia_plugin.py          # updated: proxy + editor routes
pyproject.toml             # updated: fastapi, httpx, uvicorn deps
src/
  templates/
    editor.html            # new: PyScript-powered editor
TODO.md                    # this file
```

---

## Verification

1. `uv run uvicorn sinopia_plugin:app --reload` starts without error
2. `GET /sinopia/api/resource/ed1213b5-1ccb-47e8-acd8-29db01a7e1eb` returns JSON-LD from BCLD
3. `GET /sinopia/editor/ed1213b5-1ccb-47e8-acd8-29db01a7e1eb` returns HTML (200)
4. Browser: resource title/type badge populated from JSON-LD
5. Browser: triples table shows raw RDF data
6. Browser: left nav lists BIBFRAME Work properties; items with data show `>` arrow
7. Browser: Work Title section pre-fills "Star wars" from `bf:mainTitle`
8. Browser: Copy URI button copies the resource URI to clipboard
9. Browser: tab switching works (Navigation / Versions / Relationships)
