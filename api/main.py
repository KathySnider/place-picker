"""
api/main.py
-----------
FastAPI application entry point.

Local dev:  uvicorn api.main:app --reload  (Vite dev server runs separately on :5173)
Production: uvicorn api.main:app --host 0.0.0.0 --port $PORT  (serves built frontend too)
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import search

app = FastAPI(title="place-picker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api")

@app.get("/api/health")
def health():
    return {"status": "ok"}

# Serve the built Vite frontend in production (web/dist/ exists when Docker-built)
_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        return FileResponse(os.path.join(_dist, "index.html"))
