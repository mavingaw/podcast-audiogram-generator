from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "Kinder"
    host: str = os.getenv("PAS_HOST", "0.0.0.0")
    port: int = int(os.getenv("PAS_PORT", "8080"))
    config_dir: Path = Path(os.getenv("PAS_CONFIG_DIR", "./runtime/config")).resolve()
    data_dir: Path = Path(os.getenv("PAS_DATA_DIR", "./runtime/data")).resolve()
    database_url: str | None = os.getenv("DATABASE_URL")
    session_cookie: str = "pas_session"
    # How long a sign-in lasts. Sessions used to never expire at all.
    session_days: int = int(os.getenv("PAS_SESSION_DAYS", "30"))
    allow_private_rss: bool = os.getenv("PAS_ALLOW_PRIVATE_RSS", "false").lower() == "true"
    frontend_dist: Path = Path(os.getenv("PAS_FRONTEND_DIST", "./frontend/dist")).resolve()
    max_upload_bytes: int = int(os.getenv("PAS_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
    library_dir_override: str | None = os.getenv("PAS_LIBRARY_DIR")
    models_dir_override: str | None = os.getenv("PAS_MODELS_DIR")
    whisper_model: str = os.getenv("PAS_WHISPER_MODEL", "small")
    transcribe_enabled: bool = os.getenv("PAS_TRANSCRIBE", "on").lower() not in {"off", "false", "0"}
    transcribe_language: str | None = os.getenv("PAS_TRANSCRIBE_LANGUAGE") or None
    prefer_gpu: bool = os.getenv("PAS_PREFER_GPU", "true").lower() not in {"false", "0", "off"}
    # Lets a process serve the API without also processing the queue: useful
    # for tests, and for running a second read-only instance against the same
    # database without two machines fighting over the same jobs.
    run_worker: bool = os.getenv("PAS_RUN_WORKER", "true").lower() not in {"false", "0", "off"}
    # Workers per lane. 0 means "size it from the machine" — see
    # app.services.jobs.lane_workers.
    render_workers: int = int(os.getenv("PAS_RENDER_WORKERS", "0"))

    # Local language model, used only to re-rank suggested clips by what they
    # are about. On by default now that the weights ship inside the image: a
    # feature that is already installed and switched off is a hidden feature.
    llm_enabled: bool = os.getenv("PAS_LLM", "true").strip().lower() in {"1", "true", "yes", "on"}
    # Speaker detection is bundled and ready, but does NOT run automatically:
    # left to estimate the speaker count on real podcast audio it was wrong, and
    # a confidently wrong attribution is worse than none. It runs when somebody
    # says how many people are talking, which is the one thing they know.
    diarize_enabled: bool = os.getenv("PAS_DIARIZE", "false").strip().lower() in {"1", "true", "yes", "on"}
    # Open registration. Off by default now that this can be put on the
    # internet: a public sign-up form on a reachable address means anybody with
    # the URL has an account. Invite people with PAS_SIGNUP_CODE instead, or
    # create their accounts from the admin screen.
    open_signups: bool = os.getenv("PAS_OPEN_SIGNUPS", "false").strip().lower() in {"1", "true", "yes", "on"}
    # A shared code that lets somebody sign themselves up. Simpler than issuing
    # accounts by hand and far safer than leaving registration open.
    signup_code: str | None = os.getenv("PAS_SIGNUP_CODE") or None
    # Send the session cookie only over HTTPS. Auto means "decide from the
    # request", which is right behind a tunnel that terminates TLS for us.
    cookie_secure: str = os.getenv("PAS_COOKIE_SECURE", "auto").strip().lower()

    # Watching podcast feeds. On by default, but it does nothing at all until
    # somebody adds a feed, and it never publishes anything.
    feed_polling: bool = os.getenv("PAS_FEED_POLLING", "true").strip().lower() in {"1", "true", "yes", "on"}
    feed_interval_seconds: int = int(os.getenv("PAS_FEED_INTERVAL_SECONDS", "900"))

    llm_model: str | None = os.getenv("PAS_LLM_MODEL")
    llm_model_url: str | None = os.getenv("PAS_LLM_MODEL_URL")
    # Which card to put it on. On a two-card host this is how the model and
    # Whisper are kept off each other.
    llm_gpu_index: str | None = os.getenv("PAS_LLM_GPU")
    # None means "offload everything if a GPU exists"; 0 forces CPU.
    llm_gpu_layers: int | None = (
        int(os.environ["PAS_LLM_GPU_LAYERS"]) if os.getenv("PAS_LLM_GPU_LAYERS") else None
    )
    media_workers: int = int(os.getenv("PAS_MEDIA_WORKERS", "0"))
    # Transcription stays at one by default: a second worker means a second
    # model resident in VRAM, and they would queue on the same GPU anyway.
    transcribe_workers: int = int(os.getenv("PAS_TRANSCRIBE_WORKERS", "1"))
    # FFmpeg thread counts. 0 lets FFmpeg decide, which is usually right.
    ffmpeg_threads: int = int(os.getenv("PAS_FFMPEG_THREADS", "0"))

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
    def library_dir(self) -> Path:
        """Root of the licensed sound library.

        Held outside the repository and outside the container image on purpose:
        the packs we ship against permit use, not redistribution.
        """
        if self.library_dir_override:
            return Path(self.library_dir_override).resolve()
        return self.data_dir / "library"

    @property
    def models_dir(self) -> Path:
        """Where Whisper weights live.

        Overridable because a host often already has a HuggingFace cache — the
        same weights another service downloaded — and pointing at it saves both
        the download and a second copy of a multi-gigabyte model.
        """
        if self.models_dir_override:
            return Path(self.models_dir_override).resolve()
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
        settings.library_dir,
        settings.library_dir / "music",
        settings.library_dir / "sfx",
        settings.models_dir,
        settings.secrets_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

