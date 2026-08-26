from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "Podcast Audiogram Studio"
    host: str = os.getenv("PAS_HOST", "0.0.0.0")
    port: int = int(os.getenv("PAS_PORT", "8080"))
    config_dir: Path = Path(os.getenv("PAS_CONFIG_DIR", "./runtime/config")).resolve()
    data_dir: Path = Path(os.getenv("PAS_DATA_DIR", "./runtime/data")).resolve()
    database_url: str | None = os.getenv("DATABASE_URL")
    session_cookie: str = "pas_session"
    allow_private_rss: bool = os.getenv("PAS_ALLOW_PRIVATE_RSS", "false").lower() == "true"
    frontend_dist: Path = Path(os.getenv("PAS_FRONTEND_DIST", "./frontend/dist")).resolve()

    @property
    def sqlite_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.config_dir / 'app.db'}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def models_dir(self) -> Path:
        return self.config_dir / "models"

    @property
    def secrets_dir(self) -> Path:
        return self.config_dir / "secrets"


settings = Settings()


def ensure_directories() -> None:
    for path in (
        settings.config_dir,
        settings.data_dir,
        settings.uploads_dir,
        settings.outputs_dir,
        settings.work_dir,
        settings.models_dir,
        settings.secrets_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

