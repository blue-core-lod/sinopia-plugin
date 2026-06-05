# Sinopia Airflow Plugin

Sinopia Linked Data Editor packaged as a FastAPI + PyScript Airflow plugin. The editor runs in the browser (PyScript/Pyodide) to edit BIBFRAME RDF resources and communicates with a Blue Core backend API for persistence.

## Stack

- **Backend**: FastAPI (Python 3.12) + httpx
- **Frontend**: PyScript 2024.11.1 (Pyodide) running Python in the browser
- **Templating**: Jinja2
- **RDF processing**: rdflib, pyshacl
- **UI**: Bootstrap 5, Bootstrap Icons

## Quick Start

```bash
# Run tests
uv run pytest

# Start dev server
uv run fastapi dev sinopia_plugin.py
```

## Architecture

### Backend (`sinopia_plugin.py`)
FastAPI application serving the editor UI with routes for:
- `/sinopia/editor/{resource_id}` — Editor HTML template
- `/sinopia/api/resource/{resource_id}` — Proxies JSON-LD from Blue Core API (avoids CORS)

### Frontend (`src/main.py`)
PyScript module running in browser. Key components:
- `EditorState` — Holds resource data, triples, and SHACL validation state
- `PropCardFactory` — Builds HTML cards from SHACL shapes or fallback rendering
- Jinja2 templates for HTML generation

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BLUECORE_URL` | `https://dev.bcld.info` | Blue Core API base URL |
| `ENVIRONMENT` | `` | Displayed in UI (e.g., "dev", "prod") |
| `SINOPIA_VERSION` | `4.0.0` | Version string shown in UI |

## Testing

- Unit tests: `tests/test_main.py` (126 tests)
- Integration tests: `tests/test_integration.py` (10 tests)
- **Note**: Browser modules (`pyscript`, `pyodide.http`, `js`) are mocked before importing `src/main.py`

## Known Issues

See [AGENTS.md](AGENTS.md) for detailed documentation on known limitations including nested SHACL shapes not rendering and blank node hierarchy flattening.

## Documentation

- **Agent Instructions**: [AGENTS.md](AGENTS.md) — Detailed guidance for development
- **BIBFRAME Ontology**: http://id.loc.gov/ontologies/bibframe/
- **PyScript Docs**: https://docs.pyscript.net/
- **FastAPI Docs**: https://fastapi.tiangolo.com/
