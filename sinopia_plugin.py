import pathlib

# from airflow.plugins_manager import AirflowPlugin
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

PLUGIN_DIR = pathlib.Path(__file__).parent
app = FastAPI(title="Sinopia Linked Data Editor", version="4.0.0")

@app.get("/sinopia.js")
async def serve_react_component():
    js_file_path = PLUGIN_DIR / "src/index.js"
    return FileResponse(
        path=str(js_file_path),
        media_type="application/javascript",
        filename="sinopia-app.js"
    )

@app.get("/")
async def root():
    return {
        "message": "Sinopia Plugin",
        "type": "react_app",
        "component_url": "/sinopia/app.js",
        "description": ""
    }
