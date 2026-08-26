from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Job, JobKind, JobStatus, MediaAsset, Project
from app.db.session import SessionLocal
from app.services.media import ffprobe_media, synthetic_transcript
from app.services.storage import contained_path

_worker_started = False
_lock = threading.Lock()


def start_worker_once() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, name="pas-job-worker", daemon=True)
        thread.start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        try:
            _run_one_job()
        except Exception:
            time.sleep(1)
        time.sleep(0.5)


def _run_one_job() -> None:
    with SessionLocal() as db:
        job = db.scalar(
            select(Job)
            .where(Job.status == JobStatus.queued)
            .order_by(Job.created_at.asc())
            .limit(1)
        )
        if not job:
            return
        job.status = JobStatus.running
        job.progress = 5
        job.message = "Job started"
        db.commit()
        job_id = job.id

    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                return
            if job.kind == JobKind.analyze_media:
                _analyze_media(db, job)
            elif job.kind == JobKind.transcribe:
                _transcribe(db, job)
            elif job.kind == JobKind.render:
                _render(db, job)
            elif job.kind == JobKind.model_download:
                _model_download(db, job)
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = JobStatus.failed
                job.error = str(exc)
                job.message = "Job failed"
                db.commit()


def _finish(db, job: Job, message: str, result: dict | None = None) -> None:
    job.status = JobStatus.complete
    job.progress = 100
    job.message = message
    job.result_json = json.dumps(result or {})
    db.commit()


def _step(db, job: Job, progress: int, message: str) -> None:
    job.progress = progress
    job.message = message
    db.commit()
    time.sleep(0.25)


def _analyze_media(db, job: Job) -> None:
    media = db.get(MediaAsset, job.subject_id)
    if not media:
        raise RuntimeError("Media asset not found")
    _step(db, job, 20, "Running ffprobe")
    media_path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    duration, probe_json = ffprobe_media(media_path)
    media.duration_seconds = duration
    media.probe_json = probe_json
    _finish(db, job, "Media analysis complete", {"duration_seconds": duration})


def _transcribe(db, job: Job) -> None:
    media = db.get(MediaAsset, job.subject_id)
    if not media:
        raise RuntimeError("Media asset not found")
    _step(db, job, 20, "Preparing transcription")
    _step(db, job, 55, "Generating transcript")
    media.transcript_json = json.dumps(synthetic_transcript(media.duration_seconds))
    _finish(db, job, "Transcript ready", {"media_id": media.id})


def _render(db, job: Job) -> None:
    project = db.get(Project, job.subject_id)
    if not project:
        raise RuntimeError("Project not found")
    output_dir = settings.outputs_dir / project.id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "render-manifest.json"
    _step(db, job, 20, "Building render plan")
    manifest.write_text(
        json.dumps(
            {
                "project_id": project.id,
                "title": project.title,
                "clip": {"start": project.clip_start, "end": project.clip_end},
                "aspect_ratio": project.aspect_ratio,
                "scene": json.loads(project.scene_json or "{}"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _step(db, job, 60, "Rendering preview output")
    mp4_path = output_dir / "audiogram.mp4"
    if _ffmpeg_available():
        _render_placeholder_mp4(mp4_path)
    _finish(
        db,
        job,
        "Render complete",
        {
            "manifest": str(manifest),
            "mp4": str(mp4_path) if mp4_path.exists() else None,
        },
    )


def _model_download(db, job: Job) -> None:
    _step(db, job, 30, "Checking model storage")
    models_path = settings.models_dir / (job.subject_id or "base")
    models_path.mkdir(parents=True, exist_ok=True)
    (models_path / "README.txt").write_text(
        "Model downloads are wired as background jobs. Add faster-whisper integration here.\n",
        encoding="utf-8",
    )
    _finish(db, job, "Model placeholder installed", {"path": str(models_path)})


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _render_placeholder_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=#101820:s=1080x1920:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=3",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        timeout=30,
    )

