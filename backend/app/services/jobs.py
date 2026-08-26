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
    if not project.media_id:
        raise RuntimeError("Project has no source media")
    media = db.get(MediaAsset, project.media_id)
    if not media:
        raise RuntimeError("Source media not found")

    media_path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not media_path.exists():
        raise RuntimeError("Source media file is missing")

    output_dir = settings.outputs_dir / project.id
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "render-manifest.json"
    clip_start = max(0.0, float(project.clip_start))
    clip_end = max(clip_start + 0.5, float(project.clip_end))
    duration = min(clip_end - clip_start, 600.0)
    transcript = json.loads(media.transcript_json) if media.transcript_json else synthetic_transcript(media.duration_seconds)

    _step(db, job, 20, "Building render plan")
    captions = _clip_captions(transcript, clip_start, clip_start + duration)
    srt_path = output_dir / "captions.srt"
    vtt_path = output_dir / "captions.vtt"
    ass_path = output_dir / "captions.ass"
    _write_srt(srt_path, captions)
    _write_vtt(vtt_path, captions)
    _write_ass(ass_path, captions, project.aspect_ratio)

    manifest.write_text(
        json.dumps(
            {
                "project_id": project.id,
                "media_id": media.id,
                "title": project.title,
                "source": media.original_name,
                "clip": {"start": clip_start, "end": clip_start + duration, "duration": duration},
                "aspect_ratio": project.aspect_ratio,
                "scene": json.loads(project.scene_json or "{}"),
                "outputs": {
                    "mp4": "audiogram.mp4",
                    "srt": "captions.srt",
                    "vtt": "captions.vtt",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _step(db, job, 55, "Rendering audiogram MP4")
    mp4_path = output_dir / "audiogram.mp4"
    if not _ffmpeg_available():
        raise RuntimeError("FFmpeg is not installed or not on PATH")
    _render_audiogram_mp4(
        source_path=media_path,
        output_path=mp4_path,
        ass_path=ass_path,
        aspect_ratio=project.aspect_ratio,
        clip_start=clip_start,
        duration=duration,
    )
    _finish(
        db,
        job,
        "Render complete",
        {
            "downloads": {
                "mp4": f"/api/projects/{project.id}/outputs/audiogram.mp4",
                "srt": f"/api/projects/{project.id}/outputs/captions.srt",
                "vtt": f"/api/projects/{project.id}/outputs/captions.vtt",
                "manifest": f"/api/projects/{project.id}/outputs/render-manifest.json",
            },
            "files": {
                "manifest": str(manifest),
                "mp4": str(mp4_path),
                "srt": str(srt_path),
                "vtt": str(vtt_path),
            },
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


def _render_audiogram_mp4(
    source_path: Path,
    output_path: Path,
    ass_path: Path,
    aspect_ratio: str,
    clip_start: float,
    duration: float,
) -> None:
    width, height = _dimensions(aspect_ratio)
    wave_width = int(width * 0.82)
    wave_height = max(120, int(height * 0.18))
    wave_y = int(height * 0.54)
    ass_filter = "ass=captions.ass"
    filter_complex = (
        f"[0:a]aformat=channel_layouts=stereo,showwaves=s={wave_width}x{wave_height}:"
        f"mode=line:colors=23a094,format=rgba[waves];"
        f"[1:v][waves]overlay=x=(W-w)/2:y={wave_y},{ass_filter}[v]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{clip_start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source_path),
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x101820:s={width}x{height}:r=30",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "0:a:0",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ],
        check=True,
        cwd=ass_path.parent,
        capture_output=True,
        text=True,
        timeout=max(60, int(duration * 8)),
    )


def _dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "16:9":
        return 1280, 720
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1080, 1920


def _clip_captions(transcript: dict, start: float, end: float) -> list[dict]:
    captions = []
    for segment in transcript.get("segments", []):
        segment_start = float(segment.get("start", 0.0))
        segment_end = float(segment.get("end", segment_start + 2.0))
        if segment_end <= start or segment_start >= end:
            continue
        captions.append(
            {
                "start": max(0.0, segment_start - start),
                "end": max(0.5, min(segment_end, end) - start),
                "text": str(segment.get("text", "")).strip() or " ",
            }
        )
    if captions:
        return captions
    return [{"start": 0.0, "end": max(0.5, end - start), "text": "Rendered locally with Podcast Audiogram Studio."}]


def _write_srt(path: Path, captions: list[dict]) -> None:
    blocks = []
    for index, caption in enumerate(captions, start=1):
        blocks.append(
            f"{index}\n"
            f"{_srt_timestamp(caption['start'])} --> {_srt_timestamp(caption['end'])}\n"
            f"{caption['text']}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def _write_vtt(path: Path, captions: list[dict]) -> None:
    blocks = ["WEBVTT\n"]
    for caption in captions:
        blocks.append(
            f"{_vtt_timestamp(caption['start'])} --> {_vtt_timestamp(caption['end'])}\n"
            f"{caption['text']}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def _write_ass(path: Path, captions: list[dict], aspect_ratio: str) -> None:
    width, height = _dimensions(aspect_ratio)
    font_size = 56 if height >= 1080 else 34
    margin_v = max(44, int(height * 0.08))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&HCC101820,&H99101820,0,0,0,0,100,100,0,0,3,2,0,2,80,80,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for caption in captions:
        lines.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(caption['start'])},"
            f"{_ass_timestamp(caption['end'])},"
            f"Default,,0,0,0,,{_ass_escape(caption['text'])}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _srt_timestamp(seconds: float) -> str:
    hours, rem = divmod(max(0.0, seconds), 3600)
    minutes, rem = divmod(rem, 60)
    whole = int(rem)
    millis = int(round((rem - whole) * 1000))
    return f"{int(hours):02}:{int(minutes):02}:{whole:02},{millis:03}"


def _vtt_timestamp(seconds: float) -> str:
    return _srt_timestamp(seconds).replace(",", ".")


def _ass_timestamp(seconds: float) -> str:
    hours, rem = divmod(max(0.0, seconds), 3600)
    minutes, rem = divmod(rem, 60)
    whole = int(rem)
    centis = int(round((rem - whole) * 100))
    return f"{int(hours)}:{int(minutes):02}:{whole:02}.{centis:02}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")

