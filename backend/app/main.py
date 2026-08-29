from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import ensure_directories, settings
from app.db.init_db import init_db
from app.services import retention
from app.services.jobs import start_worker_once


def create_app() -> FastAPI:
    ensure_directories()
    init_db()

    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    static_dir = Path(settings.frontend_dist)
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    @app.on_event("startup")
    def _startup() -> None:
        start_worker_once()
        # Off unless PAS_RETENTION_DAYS says otherwise, and it starts no thread
        # at all when disabled. `start` swallows its own failures: housekeeping
        # is never a reason for the application not to come up.
        retention.start()

    return app


app = create_app()

