# Agent Instructions

## Project Overview

Sinopia Linked Data Editor packaged as a [FastAPI][FASTAPI] + [PyScript][PYSCRIPT] [Airflow][AIRFLOW] plugin. 
The editor runs in the browser ([PyScript][PYSCRIPT]/[Pyodide][PYODIDE]) to edit 
[BIBFRAME][BF] RDF resources and communicates with a [Blue Core backend API][BC_API] for persistence.

**Stack:**
- **Backend**: [FastAPI][FASTAPI] (Python 3.12) + [httpx][HTTPX] for proxying [BLUECORE API][BC_API]
- **Frontend**: [PyScript][PYSCRIPT] 2024.11.1 ([Pyodide][PYODIDE]) running [Python][PYTHON] in the browser
- **Templating**: [Jinja2][JINJA] (both server-side HTML and [PyScript][PYODIDE] dynamic rendering)
- **RDF processing**: [rdflib][RDFLIB], [pyshacl][PYSHACL]
- **UI**: [Bootstrap 5][BOOTSTRAP], [Bootstrap Icons][BOOTSTRAP_ICONS]
- **Testing**: pytest, FastAPI TestClient, [BeautifulSoup4][BS4]

## Quick Commands

```bash
# Run tests (requires uv)
uv run pytest

# Start dev server (requires uv)
uv run uvicorn sinopia_plugin:app --reload
```

## Architecture

### Backend (`sinopia_plugin.py`)
[FastAPI][FASTAPI] application serving the editor UI.

**Routes:**
- `GET /sinopia/editor/{resource_id}` — Serves editor HTML template
- `GET /sinopia/api/resource/{resource_id}` — Proxies JSON-LD from BLUECORE API (avoids CORS)
- `GET /` — JSON API metadata

### Frontend (`src/main.py` - PyScript)
Runs entirely in browser (Python via [Pyodide][PYODIDE]/[PyScript][PYSCRIPT]).

**Core classes:**
- `EditorState` — Holds resource URI, types, properties, triples, SHACL validation state
- `PropShape` — Descriptor for a SHACL PropertyShape (path, name, required, class, datatype)
- `PropCardFactory` — Builds HTML prop-cards and input-cards from SHACL shapes or fallback

**Key functions:**
- `_load_shacl_graph()` — Merges Turtle from localStorage['template'] into an rdflib Graph
- `_all_shapes(shacl)` — Returns all sh:NodeShape subjects in SHACL graph
- `_prop_shapes(shacl, shape)` — Returns list of PropertyShape descriptors for a NodeShape
- `render_main_editor(state)` — Builds HTML cards from SHACL shapes or fallback
- `render_triples(state)` — Shows unused triples in a table at the bottom

**Templates ([Jinja2][JINJA]):**
- `TEMPLATE_PROP_CARD` — Container div for a property group/node-card
- `TEMPLATE_LITERAL_INPUT` — Single textarea for literal-valued properties
- `TEMPLATE_URI_INPUT` — Two-row widget (URI + Label) for URI/class-valued properties

**Data flow:**
1. Load resource JSON-LD from `/sinopia/api/resource/{id}`
2. Parse into `EditorState` (triples, props, labels, types)
3. Load SHACL shapes from localStorage['template']
4. Render UI:
   - If SHACL shapes exist: render one prop-card per sh:NodeShape, plus fallback card for unhandled properties
   - If no SHACL shapes: render single fallback card with all leaf-value properties
5. Show unused triples table if any triples remain unhandled

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BLUECORE_URL` | `https://dev.bcld.info` | Blue Core API base URL |
| `ENVIRONMENT` | `` | Displayed in UI (e.g., "dev", "prod") |
| `SINOPIA_VERSION` | `4.0.0` | Version string shown in UI |

## Testing Quirks

- Browser modules (`pyscript`, `pyodide.http`, `js`) must be mocked before importing `src/main.py`. See `tests/test_main.py` for the pattern.
- The `_entry_point()` coroutine only runs when `sys.platform == "emscripten"` ([Pyodide][PYODIDE]/[PyScript][PYSCRIPT] environment).

## Known Issues & Limitations

### Critical
1. **Nested SHACL shapes not rendering** — Shapes with `sh:targetClass bf:Title`, `bf:Instance`, etc. don't render as nested cards when their target objects appear as values in the data. Architecture assumes all shapes apply to the main resource, not to nested blank nodes.

2. **Properties with complex values hidden** — Properties like `bf:title` that point to blank node objects are stored as stringified dicts ("blobs") in state.props, then filtered out in fallback rendering. They appear in unused triples table instead of in the editor.

3. **Unused triples table shows work-level properties** — All work-level triples appear in the table even when they're displayed in the editor. The table should only show triples not covered by any SHACL shape or fallback card.

### Design Issues
- **Blank node extraction flattens hierarchy** — `_extract_props()` recursively extracts nested blank node properties and stores them at the work level (same subject), losing the nested structure. This causes Title properties to pollute work-level state.props.

- **No nested card support** — PropCardFactory doesn't know how to render SHACL shapes as nested cards within parent properties. Needs a way to identify which shapes apply to nested objects and render them inline.

- **Single-level rendering** — `render_main_editor()` iterates through all shapes and tries to render them at the top level. Nested shapes need to be rendered where their target objects appear in the data.

## Code Style Guidelines

- Use Python's `match` statement instead of `if/elif/else` chains for pattern matching
- Prefer [Jinja2][JINJA] templates for HTML generation over f-strings
- Keep templates in `TEMPLATE_*` constants at module level
- Use type hints for public methods
- Only add comments for non-obvious behavior (workarounds, constraints)
- Pull Requests should have upper limit of 500 for new lines of code

## Testing

- Unit tests in `tests/test_main.py` ([PyScript][PYSCRIPT] logic) — 126 tests
- Integration tests in `tests/test_integration.py` (HTML rendering, structure) — 10 tests
- Run all tests: `uv run pytest tests/ -q`
- **IMPORTANT**: Always run tests before git commit
- **IMPORTANT**: Create PRs and wait for user review before merging. Never allow auto-merge or merge without explicit user approval.

## File Layout

```
sinopia_plugin.py                    # FastAPI app + proxy routes
src/
  main.py                           # PyScript editor (EditorState, rendering)
  conf.json                         # PyScript config (includes jinja2 package)
  dctap_shacl.py                   # [DCTAP to SHACL][BF_DCTAP] conversion utility
  templates/
    index.html                      # Editor HTML template with PyScript loader
    _editor.html                    # Editor UI structure (two-column layout, tabs)
    base.html                       # Blue Core header/footer wrapper
tests/
  test_main.py                     # Unit tests for [PyScript][PYSHACL] logic
  test_integration.py              # Integration tests for HTML rendering
  test_dctap_shacl.py              # [DCTAP][DCTAP] conversion tests
  test_plugin.py                   # FastAPI plugin tests
```

## Dependencies

Managed via `[uv][UV]`. Python 3.12+ required. Key packages: `[fastapi][FASTAPI]`, `[httpx][HTTPX]`, `[rdflib][RDFLIB]`, `[pyshacl][PYSHACL]`, `[jinja2][JINJA]`.

[AIRFLOW]: https://airflow.apache.org/
[BC_API]: https://github.com/blue-core-lod/bluecore_api
[BF]: https://bibframe.org/
[BS4]: https://beautiful-soup-4.readthedocs.io/en/latest/
[BOOTSTRAP]: https://getbootstrap.com/
[BOOTSTRAP_ICONS]: https://icons.getbootstrap.com/
[DCTAP]: https://www.dublincore.org/specifications/dctap/
[BF_DCTAP]: https://bf-interop.github.io/DCTap/
[FASTAPI]: https://fastapi.tiangolo.com/
[HTTPX]: https://www.python-httpx.org/
[JINJA]: https://jinja.palletsprojects.com/en/stable/
[PYODIDE]: https://pyodide.org/en/stable/
[PYSCRIPT]: https://pyscript.net/
[PYSHACL]: https://github.com/rdflib/pyshacl
[PYTHON]: https://python.org
[PYTEST]: https://docs.pytest.org/en/stable/
[RDFLIB]: https://rdflib.readthedocs.io/en/stable/
[UV]: https://docs.astral.sh/uv/
