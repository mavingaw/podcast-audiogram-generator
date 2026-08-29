from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import ensure_directories, settings
from app.db.init_db import init_db
from app.services import retention, vitals
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
    # Before the router, so it wraps everything including the static mount.
    app.middleware("http")(vitals.timing_middleware)

    @app.middleware("http")
    async def cache_policy(request, call_next):
        """Stop a browser from keeping an old copy of the app past a deploy.

        The bundle files are content-hashed, so they can be cached forever.
        The page that names them cannot: served with no Cache-Control at all,
        browsers apply heuristic freshness and can keep serving yesterday's
        index.html — which loads yesterday's bundle, and every fix shipped
        since is invisible until somebody thinks to hard-refresh. Reported as
        "nothing is getting fixed", which from the outside is exactly what it
        looks like.
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not path.startswith("/api/"):
            # The HTML shell and anything unhashed next to it: always revalidate.
            response.headers["Cache-Control"] = "no-cache"
        return response
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
        # Records what the process was doing before it stopped answering. An
        # incident where every request took minutes left no trace at all in the
        # logs, because nothing failed — it was only slow.
        vitals.start()

    return app


app = create_app()

