from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update

from app.core.config import settings
from app.db.models import AppSetting, Job, JobKind, JobStatus, MediaAsset, Project, SoundAsset
from app.db.session import SessionLocal
from app.services import cancellation, cuts, loudness
from app.services.cancellation import JobCancelled
from app.services.encoders import select as select_encoder
from app.services.gpu import discover_gpus
from app.services.library import credits_for, sound_path
from app.services.media import ffprobe_media, synthetic_transcript
from app.services.music_bed import MusicBed, audio_filters
from app.services.waveform import WaveformError, extract_peaks
from app.services.waveform import duration_of as waveform_duration
from app.services.waveform import resample as resample_peaks
from app.services.music_bed import from_scene as music_bed_from_scene
from app.services.plates import Plates, bake as bake_plates
from app.services.scene import (
    CAPTION_PRESETS,
    PEAK_SHARE,
    caption_char_budget,
    ENVELOPE_STYLES,
    WAVE_STYLES,
    ass_color,
    Scene,
    enable_expression,
    escape_drawtext,
    ffmpeg_color,
    find_font_file,
    showwaves_colors,
)
from app.services.scene import parse as parse_scene
from app.services.speakers import colour_for as speaker_colour
from app.services.transcription import TranscriptionError, choose_runtime
from app.services.transcription import available as transcription_available
from app.services.transcription import caption_lines
from app.services.transcription import transcribe as run_transcription
from app.services.storage import contained_path

_workers_started = False
_lock = threading.Lock()

# Job kinds are grouped into lanes, and each lane gets its own worker thread.
#
# One shared queue meant a render and a transcribe could never overlap — the
# second simply waited — so a host with a card for each job was using one at a
# time. Splitting by kind lets the 4090 transcribe while the encoder GPU
# renders, and keeps the cheap probing jobs from queueing behind either.
#
# The lanes are deliberately serial *within* themselves: two concurrent renders
# would compete for the same encoder session and the same CPU-bound filter
# graph, which is slower than doing them in order.
LANES: dict[str, tuple[JobKind, ...]] = {
    # Feeds get their own lane: reading a feed is quick, but downloading an
    # episode is minutes of somebody else's bandwidth, and neither should sit
    # in front of a render.
    "feeds": (JobKind.check_feeds, JobKind.import_episode),
    "media": (JobKind.analyze_media, JobKind.waveform, JobKind.model_download),
    "transcribe": (JobKind.transcribe,),
    "render": (JobKind.render,),
}


def lane_workers(lane: str) -> int:
    """How many threads a lane runs.

    A single render uses about four threads: FFmpeg's filter graph is a long
    serial chain — overlay, then a drawbox per waveform bar, then subtitles,
    then text — and a chain does not parallelise however many cores it is
    offered. Measured on a 32-core host, one render peaked at 376% CPU, or
    under 6% of the machine.

    So the way to use a big box is more renders at once, not a wider one. Two
    limits decide how many: cores, and NVENC sessions — consumer cards cap
    concurrent encodes, and exceeding it fails the job rather than queueing it.
    Four is comfortably inside every current limit.

    Transcription stays at one. A second worker is a second model resident in
    VRAM, and both would serialise on the same GPU regardless.
    """
    configured = {
        "render": settings.render_workers,
        "media": settings.media_workers,
        "transcribe": settings.transcribe_workers,
    }.get(lane, 1)
    if configured > 0:
        return configured

    cores = os.cpu_count() or 4
    # Roughly one worker per eight threads, so a small box stays at one.
    return max(1, min(4, cores // 8))

_HANDLERS = {
    JobKind.analyze_media: lambda db, job: _analyze_media(db, job),
    JobKind.waveform: lambda db, job: _waveform(db, job),
    JobKind.transcribe: lambda db, job: _transcribe(db, job),
    JobKind.render: lambda db, job: _render(db, job),
    JobKind.model_download: lambda db, job: _model_download(db, job),
    JobKind.check_feeds: lambda db, job: _check_feeds(db, job),
    JobKind.import_episode: lambda db, job: _import_episode(db, job),
}


def start_worker_once() -> None:
    global _workers_started
    if not settings.run_worker:
        return
    with _lock:
        if _workers_started:
            return
        _requeue_interrupted_jobs()
        _clear_stale_scratch()
        _warm_language_model()
        _start_feed_schedule()
        for lane, kinds in LANES.items():
            for index in range(lane_workers(lane)):
                thread = threading.Thread(
                    target=_worker_loop,
                    args=(lane, kinds),
                    name=f"pas-job-{lane}-{index}",
                    daemon=True,
                )
                thread.start()
        _workers_started = True


def _warm_language_model() -> None:
    """Load the clip-scoring model in the background at start-up.

    Loading is the expensive part — several seconds — and doing it inside the
    first request makes asking for suggestions feel broken the first time and
    instant afterwards. Warming it on a daemon thread means start-up is not held
    up either, and a failure is logged inside `load()` rather than raised.
    """
    try:
        from app.services import llm

        if not llm.available():
            return
        threading.Thread(target=llm.load, name="pas-llm-warm", daemon=True).start()
    except Exception:
        # Warming is an optimisation; never let it stop the workers.
        pass


def _start_feed_schedule() -> None:
    """Queue a feed check periodically, for every user watching one.

    A daemon thread rather than cron: this has to work inside one container with
    no host configuration, and the check itself is a normal job, so it queues
    behind whatever else is running instead of competing with it.
    """
    if not settings.feed_polling:
        return

    def loop() -> None:
        from app.db.models import Feed

        # A pause before the first sweep: start-up is busy enough without a
        # burst of network reads competing with the model loading.
        time.sleep(60)
        while True:
            try:
                with SessionLocal() as db:
                    owners = {
                        feed.owner_id
                        for feed in db.scalars(
                            select(Feed).where(Feed.active.is_(True))
                        ).all()
                    }
                    for owner in owners:
                        # Never stack checks: if the last one is still queued or
                        # running, this sweep has nothing useful to add.
                        pending = db.scalar(
                            select(Job.id).where(
                                Job.owner_id == owner,
                                Job.kind == JobKind.check_feeds,
                                Job.status.in_([JobStatus.queued, JobStatus.running]),
                            )
                        )
                        if pending:
                            continue
                        db.add(Job(
                            owner_id=owner, kind=JobKind.check_feeds,
                            message="Scheduled feed check",
                        ))
                    if owners:
                        db.commit()
            except Exception as error:
                log.warning("Feed schedule failed: %s", error)
            time.sleep(max(60, settings.feed_interval_seconds))

    threading.Thread(target=loop, name="pas-feed-schedule", daemon=True).start()


def _requeue_interrupted_jobs() -> None:
    """Put jobs that were mid-flight at shutdown back in the queue.

    Called once, before any worker starts, so anything still marked `running` at
    that moment was interrupted — by a restart, a crash, or a container
    replacement. Left alone it stays `running` forever and the UI waits on
    something nobody is doing. A container gets restarted; the queue has to
    survive it.
    """
    try:
        with SessionLocal() as db:
            stranded = db.scalars(select(Job).where(Job.status == JobStatus.running)).all()
            for job in stranded:
                job.status = JobStatus.queued
                job.progress = 0
                job.message = "Requeued after restart"
            if stranded:
                db.commit()
    except Exception:
        # Never let recovery stop the workers from starting.
        pass


def _clear_stale_scratch() -> None:
    """Empty the working directory at start-up.

    Nothing in it belongs to a live job — this runs before any worker does — so
    whatever survived a crash or a container replacement is garbage taking up
    the data volume.
    """
    try:
        if not settings.work_dir.exists():
            return
        for leftover in settings.work_dir.iterdir():
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)
    except Exception:
        # Housekeeping must never stop the workers from starting.
        pass


def _worker_loop(lane: str, kinds: tuple[JobKind, ...]) -> None:
    while True:
        try:
            worked = _run_one_job(kinds)
        except Exception:
            worked = False
            time.sleep(1)
        # Poll quickly while there is a backlog, slowly when idle: several
        # lanes each waking twice a second is a lot of pointless SQLite reads.
        time.sleep(0.2 if worked else 1.0)


def _claim_job(kinds: tuple[JobKind, ...]) -> str | None:
    """Take the next queued job of these kinds, or return None.

    Fair-share, not first-come-first-served. Strict FIFO is fine for one person
    and hostile the moment the instance is shared: someone queueing ten clips
    takes every lane, and everybody else waits behind all ten. Ordering by how
    much of the lane an owner is *already* using means a person with nothing
    running is served before someone who is mid-batch, while a single user still
    gets their own jobs in the order they asked for them.

    The claim itself is a conditional UPDATE rather than a read followed by a
    write: with several lanes polling the same table, two of them can read the
    same row before either writes. Only the update whose `WHERE status = queued`
    still matches wins, and the loser looks again.
    """
    with SessionLocal() as db:
        # How much of this lane each owner is running right now.
        running = (
            select(Job.owner_id.label("owner_id"), func.count().label("active"))
            .where(Job.status == JobStatus.running, Job.kind.in_(kinds))
            .group_by(Job.owner_id)
            .subquery()
        )
        candidate = db.scalar(
            select(Job.id)
            .outerjoin(running, running.c.owner_id == Job.owner_id)
            .where(Job.status == JobStatus.queued, Job.kind.in_(kinds))
            .order_by(
                func.coalesce(running.c.active, 0).asc(),
                Job.created_at.asc(),
            )
            .limit(1)
        )
        if candidate is None:
            return None
        claimed = db.execute(
            update(Job)
            .where(Job.id == candidate, Job.status == JobStatus.queued)
            .values(status=JobStatus.running, progress=5, message="Job started")
        )
        db.commit()
        return candidate if claimed.rowcount == 1 else None


def _run_one_job(kinds: tuple[JobKind, ...]) -> bool:
    """Run one job from these kinds. Returns whether there was work."""
    job_id = _claim_job(kinds)
    if job_id is None:
        return False

    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                return True
            handler = _HANDLERS.get(job.kind)
            if handler is None:
                raise RuntimeError(f"No handler for job kind {job.kind}")
            handler(db, job)
    except JobCancelled:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = JobStatus.canceled
                job.message = "Cancelled"
                job.error = None
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = JobStatus.failed
                job.error = str(exc)
                job.message = "Job failed"
                db.commit()
    finally:
        cancellation.clear(job_id)
    return True


def _finish(db, job: Job, message: str, result: dict | None = None) -> None:
    job.status = JobStatus.complete
    job.progress = 100
    job.message = message
    job.result_json = json.dumps(result or {})
    db.commit()


def _step(db, job: Job, progress: int, message: str) -> None:
    # Steps double as cancellation checkpoints, so a job stops between phases
    # even when nothing is currently inside a killable subprocess.
    cancellation.raise_if_cancelled(job.id)
    job.progress = progress
    job.message = message
    db.commit()


def _check_feeds(db, job: Job) -> None:
    """Read every feed that is due, and queue anything new.

    One job for all of a user's feeds rather than one each: reading a feed is
    cheap, and a single job is far easier to reason about in the queue than a
    dozen that mostly do nothing.
    """
    from app.db.models import Feed, FeedEpisode
    from app.services import feeds as feedservice

    watched = db.scalars(
        select(Feed).where(Feed.owner_id == job.owner_id, Feed.active.is_(True))
    ).all()
    if not watched:
        _finish(db, job, "No feeds are being watched", {"feeds": 0})
        return

    queued, checked, errors = 0, 0, []
    for index, feed in enumerate(watched):
        if not feedservice.due(feed.last_checked):
            continue
        _step(db, job, 10 + int(80 * index / max(1, len(watched))), f"Reading {feed.title}")
        try:
            parsed, etag, modified, changed = feedservice.fetch(
                feed.url, feed.etag, feed.last_modified
            )
        except Exception as error:
            feed.last_error = str(error)[:500]
            feed.last_checked = datetime.now(timezone.utc)
            errors.append(f"{feed.title}: {error}")
            db.commit()
            continue

        checked += 1
        feed.etag, feed.last_modified = etag, modified
        feed.last_checked = datetime.now(timezone.utc)
        feed.last_error = None
        if getattr(parsed.feed, "title", None):
            feed.title = str(parsed.feed.title)[:200]

        if changed:
            known = {
                row.guid
                for row in db.scalars(
                    select(FeedEpisode).where(FeedEpisode.feed_id == feed.id)
                ).all()
            }
            found = feedservice.episodes_of(parsed)
            fresh = [item for item in found if item.guid not in known]

            # First sight of a feed: take only the newest. Subscribing to a show
            # with 400 back-episodes must not enqueue 400 transcriptions.
            if not known:
                fresh = fresh[: feedservice.FIRST_RUN_LIMIT]

            for item in fresh:
                record = FeedEpisode(
                    feed_id=feed.id, guid=item.guid, title=item.title,
                    published=item.published, enclosure_url=item.url,
                )
                db.add(record)
                db.flush()
                db.add(Job(
                    owner_id=job.owner_id, kind=JobKind.import_episode,
                    subject_id=record.id, message=f"Queued {item.title[:60]}",
                ))
                queued += 1
            if fresh:
                feed.last_guid = fresh[0].guid
        db.commit()

    message = f"Checked {checked} feed(s); {queued} new episode(s)"
    if errors:
        message += f"; {len(errors)} failed"
    _finish(db, job, message, {"checked": checked, "queued": queued, "errors": errors})


def _import_episode(db, job: Job) -> None:
    """Download one episode and put it through the pipeline.

    Nothing is published at the end of this. The clips are prepared and left in
    the library for a person to look at, which is the whole reason automation
    like this is tolerable to a creator.
    """
    from app.db.models import Feed, FeedEpisode
    from app.services import feeds as feedservice

    record = db.get(FeedEpisode, job.subject_id)
    if not record:
        raise RuntimeError("Episode not found")
    feed = db.get(Feed, record.feed_id)
    if not feed:
        raise RuntimeError("Feed not found")

    _step(db, job, 5, f"Downloading {record.title[:50]}")
    stored = f"{record.id}-{feedservice.filename_for(feedservice.Episode(guid=record.guid, title=record.title, published=record.published, url=record.enclosure_url))}"
    target = contained_path(settings.uploads_dir, settings.uploads_dir / stored)

    def report(fraction: float) -> None:
        percent = 5 + int(fraction * 45)
        if percent >= (job.progress or 0) + 5:
            job.progress = percent
            db.commit()

    try:
        size = feedservice.download(record.enclosure_url, target, on_progress=report)
    except Exception as error:
        record.status = "failed"
        record.error = str(error)[:500]
        db.commit()
        raise RuntimeError(f"Could not download the episode: {error}") from error

    media = MediaAsset(
        owner_id=job.owner_id,
        original_name=record.title[:200] or stored,
        stored_name=stored,
        content_type="audio/mpeg",
        size_bytes=size,
    )
    db.add(media)
    db.flush()
    record.media_id = media.id
    record.status = "imported"

    # The same follow-on work an upload gets.
    for kind in (JobKind.analyze_media, JobKind.waveform, JobKind.transcribe):
        db.add(Job(owner_id=job.owner_id, kind=kind, subject_id=media.id,
                   message=f"Queued {kind.value}"))
    db.commit()

    _step(db, job, 60, "Queued transcription")
    _finish(
        db, job,
        f"Imported {record.title[:60]}",
        {
            "media_id": media.id,
            "feed_id": feed.id,
            "size_bytes": size,
            # Clip cutting waits for the transcript, so it is queued by the
            # transcribe job rather than here.
            "clip_count": feed.clip_count,
        },
    )


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


def _waveform(db, job: Job) -> None:
    media = db.get(MediaAsset, job.subject_id)
    if not media:
        raise RuntimeError("Media asset not found")
    media_path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not media_path.exists():
        raise RuntimeError("Source media file is missing")

    _step(db, job, 25, "Decoding audio")
    try:
        envelope = extract_peaks(media_path, job_id=job.id)
    except WaveformError as error:
        raise RuntimeError(str(error)) from error

    _step(db, job, 80, "Reducing peaks")
    media.peaks_json = json.dumps(envelope)
    # ffprobe can be wrong or absent for streamed uploads; the decoded envelope
    # is measured from the samples themselves, so prefer it when they disagree.
    if not media.duration_seconds and envelope.get("duration"):
        media.duration_seconds = float(envelope["duration"])
    _finish(db, job, "Waveform ready", {"media_id": media.id, "buckets": envelope["count"]})


def _transcribe(db, job: Job) -> None:
    media = db.get(MediaAsset, job.subject_id)
    if not media:
        raise RuntimeError("Media asset not found")
    media_path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not media_path.exists():
        raise RuntimeError("Source media file is missing")

    stored_model = _setting(db, "whisper_model") or settings.whisper_model
    stored_language = _setting(db, "whisper_language") or settings.transcribe_language
    stored_enabled = _setting(db, "transcribe_enabled")
    enabled = settings.transcribe_enabled if stored_enabled is None else stored_enabled == "true"

    if not enabled:
        _finish(db, job, "Transcription is disabled", {"media_id": media.id, "skipped": True})
        return

    if not transcription_available():
        # Better a usable clip with placeholder captions than a failed job, but
        # say plainly that these are not the real words.
        media.transcript_json = json.dumps(synthetic_transcript(media.duration_seconds))
        _finish(
            db, job,
            "Placeholder transcript: faster-whisper is not installed",
            {"media_id": media.id, "placeholder": True},
        )
        return

    gpu_index = _transcription_gpu_index(db)
    runtime = choose_runtime(stored_model, prefer_gpu=settings.prefer_gpu, device_index=gpu_index)
    _step(db, job, 10, f"Loading {runtime.model_size} model on {runtime.device}")

    # The worker commits progress from the job thread; keep it cheap so a long
    # episode reports movement without hammering SQLite.
    last = {"percent": 10}

    def report(fraction: float) -> None:
        percent = 10 + int(fraction * 85)
        if percent >= last["percent"] + 5:
            last["percent"] = percent
            job.progress = percent
            job.message = f"Transcribing on {runtime.device}"
            db.commit()

    try:
        transcript = run_transcription(
            media_path,
            language=stored_language,
            model_size=stored_model,
            prefer_gpu=settings.prefer_gpu,
            device_index=gpu_index,
            on_progress=report,
        )
    except TranscriptionError as error:
        raise RuntimeError(str(error)) from error

    speakers_found = _detect_speakers(db, job, media_path, transcript)
    media.transcript_json = json.dumps(transcript)
    db.commit()
    clipped = _auto_clip(db, job, media)

    media.transcript_json = json.dumps(transcript)
    words = sum(len(segment.get("words") or []) for segment in transcript["segments"])
    detail = f"{transcript['language']}, {len(transcript['segments'])} segments"
    if speakers_found > 1:
        detail += f", {speakers_found} speakers"
    if clipped:
        detail += f", {clipped} clips prepared"
    _finish(
        db, job,
        f"Transcript ready ({detail})",
        {
            "media_id": media.id,
            "language": transcript["language"],
            "segments": len(transcript["segments"]),
            "words": words,
            "speakers": speakers_found,
            "model": transcript.get("model"),
            "device": transcript.get("device"),
            "device_index": transcript.get("device_index"),
        },
    )


def _auto_clip(db, job: Job, media: MediaAsset) -> int:
    """Cut clips from an episode that arrived through a watched feed.

    Only for feed imports, and only when that feed asks for it. An upload is
    somebody sitting at the screen who can press the button themselves; a feed
    import happens while nobody is looking, which is exactly when having the
    clips already waiting is worth something.

    Nothing is published. The clips land in the library like any others.
    """
    from app.db.models import Feed, FeedEpisode

    try:
        episode = db.scalar(select(FeedEpisode).where(FeedEpisode.media_id == media.id))
        if not episode:
            return 0
        feed = db.get(Feed, episode.feed_id)
        if not feed or feed.clip_count < 1:
            return 0

        from app.services.batching import make_clips

        made = make_clips(
            db,
            owner_id=job.owner_id,
            media=media,
            count=feed.clip_count,
            aspect_ratio=feed.aspect_ratio,
            template_id=feed.template_id,
            # Rendering without being asked is the thing creators are wary of,
            # so it is opt-in per feed.
            render=feed.auto_render,
            source="feed",
            review_state="pending",
        )
        return len(made)
    except Exception as error:
        # A transcript that arrived is worth keeping even if the clips failed.
        log.warning("Automatic clipping failed for %s: %s", media.id, error)
        return 0


def _detect_speakers(db, job: Job, media_path: Path, transcript: dict) -> int:
    """Label the transcript with who is speaking, if that is possible here.

    Runs after transcription rather than alongside it because it needs the
    segment boundaries to attribute. A failure is not a failed transcript — a
    transcript without speaker labels is exactly what this app produced before —
    so anything going wrong here is logged and the job still succeeds.
    """
    if not settings.diarize_enabled:
        return 1
    try:
        from app.services import diarization

        if not diarization.available():
            return 1
        _step(db, job, 95, "Identifying speakers")
        result = diarization.analyse(media_path)
        diarization.apply(transcript, result)
        return result.speaker_count
    except Exception as error:
        log.warning("Speaker detection failed: %s", error)
        return 1


def _render(db, job: Job) -> None:
    project = db.get(Project, job.subject_id)
    if not project:
        raise RuntimeError("Project not found")
    work_dir = settings.work_dir / f"render-{job.id}"
    try:
        with _project_lock(project.id):
            _render_locked(db, job, project, work_dir)
    finally:
        # A cancelled or failed render used to leave its scratch behind — the
        # baked plates and a half-written MP4, which for a long clip is
        # hundreds of megabytes nobody ever collects.
        shutil.rmtree(work_dir, ignore_errors=True)


def _render_locked(db, job: Job, project: Project, work_dir: Path) -> None:
    if not project.media_id:
        raise RuntimeError("Project has no source media")
    media = db.get(MediaAsset, project.media_id)
    if not media:
        raise RuntimeError("Source media not found")

    media_path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
    if not media_path.exists():
        raise RuntimeError("Source media file is missing")

    # Every render works in its own directory and publishes at the end.
    #
    # Renders run concurrently now, and two of the same project used to share
    # both a scratch directory and an output path: several FFmpeg processes
    # writing one `audiogram.mp4`, each reading whichever `captions.ass` won the
    # last write. The result was whatever finished last, and it was luck that it
    # was ever coherent.
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir = settings.outputs_dir / project.id
    manifest = work_dir / "render-manifest.json"
    clip_start = max(0.0, float(project.clip_start))
    clip_end = max(clip_start + 0.5, float(project.clip_end))
    warnings: list[str] = []

    # A clip that runs past the end of the source used to render happily as a
    # frozen frame over silence — a 74 KB file of nothing, with no indication
    # anything was wrong. Clamp it, and say so.
    source_seconds = media.duration_seconds or waveform_duration(media.peaks_json)
    if source_seconds and source_seconds > 0:
        if clip_start >= source_seconds:
            raise RuntimeError(
                f"The clip starts at {clip_start:.1f}s but the source is only "
                f"{source_seconds:.1f}s long."
            )
        if clip_end > source_seconds:
            warnings.append(
                f"clip shortened to the end of the source ({source_seconds:.1f}s)"
            )
            clip_end = source_seconds

    duration = min(clip_end - clip_start, 600.0)
    clip_end = clip_start + duration
    transcript = json.loads(media.transcript_json) if media.transcript_json else synthetic_transcript(media.duration_seconds)

    scene = json.loads(project.scene_json or "{}")

    # Words struck out of the transcript are removed from the audio here, in a
    # pre-pass, and everything after this point works on the shortened file
    # without knowing anything was taken out. See services/cuts.py for why the
    # mapping lives in one place rather than in each downstream stage.
    cut_spans = cuts.parse(scene.get("cuts"), clip_start, clip_end)
    keep_spans: list[cuts.Span] = []
    if cut_spans:
        keep_spans = cuts.kept(cut_spans, clip_start, clip_end)
        remaining = cuts.duration(keep_spans)
        if remaining < cuts.MIN_REMAINING:
            raise RuntimeError(
                "There is almost nothing left after the cuts "
                f"({remaining:.1f}s). Restore some words and try again."
            )
        _step(db, job, 15, "Applying transcript cuts")
        cut_audio = work_dir / "cut.wav"
        cuts.extract(media_path, cut_audio, keep_spans)
        # Peaks are resampled per surviving span rather than sliced out of the
        # clip array: a two-word cut is a fraction of one bar in a 240-bar
        # waveform, and slicing would round it away entirely.
        clip_peaks = []
        for span in keep_spans:
            share = max(1, round(240 * span.length / remaining))
            clip_peaks.extend(
                resample_peaks(media.peaks_json, share, span.start, span.end)
            )
        transcript = cuts.remap_transcript(transcript, keep_spans)
        removed = duration - remaining
        warnings.append(
            f"{removed:.1f}s removed by {len(cut_spans)} transcript cut"
            + ("s" if len(cut_spans) != 1 else "")
        )
        # From here on the source is the cut file and its timeline starts at 0.
        media_path = cut_audio
        clip_start = 0.0
        duration = remaining
        clip_end = remaining

    parsed_scene = parse_scene(scene, duration)
    bed, music_file, bed_credits = _resolve_music_bed(db, scene)
    # Dropping the bed keeps the clip renderable, but silently producing a
    # different video than the editor showed is worse than a slow render, so
    # say so in the job message the UI displays.
    if scene.get("music") and bed is None:
        warnings.append("music bed skipped: the track is missing from the library")
    image_paths = _resolve_scene_images(db, parsed_scene, project.owner_id)
    # Still layers are baked once here rather than filtered on every frame.
    plates = bake_plates(
        parsed_scene, image_paths, *_dimensions(project.aspect_ratio), work_dir
    )
    # The envelope styles draw from the clip's own peaks, so the waveform
    # matches exactly what the clipper showed when the clip was chosen.
    if not keep_spans:
        clip_peaks = resample_peaks(
            media.peaks_json, 240, clip_start, clip_start + duration
        )

    _step(db, job, 20, "Building render plan")
    captions = _clip_captions(
        transcript, clip_start, clip_start + duration,
        max_chars=caption_char_budget(parsed_scene.caption_preset),
    )
    srt_path = work_dir / "captions.srt"
    vtt_path = work_dir / "captions.vtt"
    ass_path = work_dir / "captions.ass"
    _write_srt(srt_path, captions)
    _write_vtt(vtt_path, captions)
    _write_ass(ass_path, captions, project.aspect_ratio, parsed_scene)

    manifest.write_text(
        json.dumps(
            {
                "project_id": project.id,
                "media_id": media.id,
                "title": project.title,
                "source": media.original_name,
                "clip": {
                    "start": clip_start, "end": clip_start + duration,
                    "duration": duration,
                    # In source time, so the manifest describes the episode the
                    # clip came from rather than the file that was rendered.
                    "cuts": [
                        {"start": round(span.start, 3), "end": round(span.end, 3)}
                        for span in cut_spans
                    ],
                },
                "aspect_ratio": project.aspect_ratio,
                "scene": scene,
                "music": _music_manifest(bed, music_file),
                "credits": bed_credits,
                "outputs": {
                    "mp4": "audiogram.mp4",
                    "srt": "captions.srt",
                    "vtt": "captions.vtt",
                    "credits": "CREDITS.txt" if bed_credits else None,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    credits_path = work_dir / "CREDITS.txt"
    if bed_credits:
        _write_credits(credits_path, project.title, bed_credits)
    else:
        credits_path.unlink(missing_ok=True)
    _step(db, job, 55, "Rendering audiogram MP4")
    mp4_path = work_dir / "audiogram.mp4"
    if not _ffmpeg_available():
        raise RuntimeError("FFmpeg is not installed or not on PATH")
    _render_audiogram_mp4(
        source_path=media_path,
        output_path=mp4_path,
        ass_path=ass_path,
        aspect_ratio=project.aspect_ratio,
        clip_start=clip_start,
        duration=duration,
        bed=bed,
        music_path=music_file,
        scene=scene,
        image_paths=image_paths,
        peaks=clip_peaks,
        gpu_index=_encoding_gpu_index(db),
        plates=plates,
        job_id=job.id,
    )
    _publish_render(work_dir, output_dir)

    downloads = {
        "mp4": f"/api/projects/{project.id}/outputs/audiogram.mp4",
        "srt": f"/api/projects/{project.id}/outputs/captions.srt",
        "vtt": f"/api/projects/{project.id}/outputs/captions.vtt",
        "manifest": f"/api/projects/{project.id}/outputs/render-manifest.json",
    }
    if bed_credits:
        downloads["credits"] = f"/api/projects/{project.id}/outputs/CREDITS.txt"
    _finish(
        db,
        job,
        "Render complete" + (f" ({'; '.join(warnings)})" if warnings else ""),
        {
            "downloads": downloads,
            "warnings": warnings,
            "credits": bed_credits,
            "files": {
                "manifest": str(output_dir / manifest.name),
                "mp4": str(output_dir / mp4_path.name),
                "srt": str(output_dir / srt_path.name),
                "vtt": str(output_dir / vtt_path.name),
            },
        },
    )


def _setting(db, key: str) -> str | None:
    """An admin-set value, or None to fall back to the environment default."""
    row = db.get(AppSetting, key)
    return row.value if row and row.value else None


def _gpu_index_for(db, setting_key: str) -> str | None:
    """The nvidia-smi index of the GPU an admin assigned to a role.

    Settings store a UUID because indices are reordered by the driver, but
    FFmpeg's -gpu flag wants the index, so it is resolved at render time.
    """
    setting = db.get(AppSetting, setting_key)
    if setting is None or not setting.value:
        return None
    for gpu in discover_gpus():
        if gpu.get("uuid") == setting.value:
            return str(gpu.get("index"))
    return None


def _encoding_gpu_index(db) -> str | None:
    return _gpu_index_for(db, "encoding_gpu_uuid")


def _transcription_gpu_index(db) -> int | None:
    index = _gpu_index_for(db, "transcription_gpu_uuid")
    return int(index) if index is not None and index.isdigit() else None


# One render per project at a time. Two renders of the same project produce
# the same file, so the second is wasted work at best; serialising them also
# means the published directory is only ever replaced by a complete render.
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(project_id, threading.Lock())


def _publish_render(work_dir: Path, output_dir: Path) -> None:
    """Move a finished render into place, replacing whatever was there.

    Files are moved individually rather than swapping directories: a viewer may
    be streaming the previous MP4, and on Windows an open file cannot be
    renamed out from under a reader.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for produced in sorted(work_dir.iterdir()):
        if produced.is_dir() or produced.name.startswith("plate-"):
            # Plates are an implementation detail of the render, not an output.
            continue
        if produced.name == "cut.wav":
            # The transcript-cut audio is scratch, and it is uncompressed: left
            # in place it would park a hundred megabytes per project in the
            # outputs directory forever, downloadable and useless.
            continue
        target = output_dir / produced.name
        target.unlink(missing_ok=True)
        shutil.move(str(produced), str(target))


def _resolve_scene_images(db, parsed: Scene, owner_id: str) -> dict[str, Path]:
    """Map the scene's referenced image ids to files on disk.

    A missing or someone else's image is dropped rather than failing the render:
    the clip is still worth producing without its artwork.
    """
    wanted = {layer.media_id for layer in parsed.image_layers() if layer.media_id}
    if parsed.background_image.has_image:
        wanted.add(parsed.background_image.media_id)

    resolved: dict[str, Path] = {}
    for media_id in wanted:
        media = db.get(MediaAsset, media_id)
        if media is None or media.owner_id != owner_id:
            continue
        path = contained_path(settings.uploads_dir, settings.uploads_dir / media.stored_name)
        if path.exists():
            resolved[media_id] = path
    return resolved


def _resolve_music_bed(db, scene: dict) -> tuple[MusicBed | None, Path | None, list[dict]]:
    """Look up the scene's music bed and the credits its licence requires.

    A missing or de-registered track downgrades the render to voice-only rather
    than failing it: the clip is still worth producing without the bed.
    """
    bed = music_bed_from_scene(scene)
    if bed is None:
        return None, None, []
    sound = db.get(SoundAsset, bed.sound_id)
    if sound is None:
        return None, None, []
    path = sound_path(sound)
    if not path.exists():
        return None, None, []
    return bed, path, credits_for(db, [sound.id])


def _music_manifest(bed: MusicBed | None, music_file: Path | None) -> dict | None:
    if bed is None or music_file is None:
        return None
    return {
        "sound_id": bed.sound_id,
        "file": music_file.name,
        "gain_db": bed.gain_db,
        "duck_db": bed.duck_db,
        "fade_in_seconds": bed.fade_in,
        "fade_out_seconds": bed.fade_out,
        "start_offset_seconds": bed.start_offset,
        "loop": bed.loop,
    }


def _write_credits(path: Path, title: str, entries: list[dict]) -> None:
    """Write the attribution the sound licences require alongside the export."""
    lines = [f"Credits for: {title}", ""]
    for entry in entries:
        lines.append(entry["attribution"])
        lines.append(f"  Pack:    {entry['pack']}")
        lines.append(f"  Licence: {entry['license']}")
        for track in entry["tracks"]:
            lines.append(f"  Track:   {track}")
        lines.append("")
    lines.append("Rendered with Kinder.")
    path.write_text("\n".join(lines), encoding="utf-8")


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


def measure_loudness(
    source_path: Path,
    clip_start: float,
    duration: float,
    bed: "MusicBed | None" = None,
    music_path: Path | None = None,
    job_id: str | None = None,
    scene: dict | None = None,
) -> dict | None:
    """Measure the finished mix so the encode can hit a known level.

    Only the audio inputs are opened. The render's own graph carries the video
    filters in the same list, and dragging those through an analysis pass would
    cost as much as the render itself for no benefit.

    The lavfi colour source is included purely to keep input indices aligned:
    `audio_filters` addresses the music bed as input 2, because that is where it
    sits in the render command.
    """
    has_music = bed is not None and music_path is not None
    shape = parse_scene(scene, duration)
    chains, label = audio_filters(
        bed or MusicBed(sound_id=""),
        duration,
        has_music_input=has_music,
        voice_gain_db=shape.voice_gain_db,
        voice_fade_in=shape.fade_in,
        voice_fade_out=shape.fade_out,
    )

    input_args = [
        "-ss", f"{clip_start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_path),
        # Never mapped, never pulled from — it only holds index 1.
        "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1",
    ]
    if has_music:
        if bed.loop:
            input_args += ["-stream_loop", "-1"]
        input_args += ["-i", str(music_path)]

    return loudness.measure(
        "ffmpeg", input_args, chains, label, duration, job_id
    )


def build_render_command(
    source_path: Path,
    output_path: Path,
    aspect_ratio: str,
    clip_start: float,
    duration: float,
    bed: MusicBed | None = None,
    music_path: Path | None = None,
    scene: dict | None = None,
    font_file: Path | None = None,
    image_paths: dict[str, Path] | None = None,
    peaks: list[float] | None = None,
    encoder: "Encoder | None" = None,
    gpu_index: str | None = None,
    plates: "Plates | None" = None,
    loudness_measurement: dict | None = None,
) -> list[str]:
    """Assemble the FFmpeg invocation for one audiogram render.

    Split out from the subprocess call so the filter graph can be asserted on
    without shelling out.
    """
    width, height = _dimensions(aspect_ratio)
    parsed = parse_scene(scene, duration)

    has_music = bed is not None and music_path is not None
    audio_chains, audio_label = audio_filters(
        bed or MusicBed(sound_id=""),
        duration,
        has_music_input=has_music,
        voice_gain_db=parsed.voice_gain_db,
        voice_fade_in=parsed.fade_in,
        voice_fade_out=parsed.fade_out,
    )
    if loudness_measurement:
        # Applied to the finished mix, after the bed, because the level the
        # platform measures is the level of the whole file.
        audio_chains.append(
            f"{audio_label}{loudness.apply_filter(loudness_measurement)}[anorm]"
        )
        audio_label = "[anorm]"

    plates = plates or Plates()
    images = image_paths or {}
    background_path = plates.background

    # Input order: 0 source, 1 colour plate, [2 music], [background image],
    # then one input per artwork layer.
    next_input = 3 if has_music else 2
    background_input = None
    if background_path is not None:
        background_input = next_input
        next_input += 1

    image_layers = [layer for layer in parsed.image_layers() if plates.for_layer(layer.id)]
    first_image_input = next_input

    video_label = "[1:v]"

    if background_input is not None:
        # The plate is already scaled, blurred and dimmed to exactly the canvas
        # size, so this is a plain composite with no per-frame filter work.
        audio_chains.append(f"{video_label}[{background_input}:v]overlay=x=0:y=0[vbg]")
        video_label = "[vbg]"

    # Artwork plates are pre-cropped and pre-masked; only the placement is left.
    for offset, layer in enumerate(image_layers):
        index = first_image_input + offset
        x, y, _, _ = layer.pixels(width, height)
        overlay = f"{video_label}[{index}:v]overlay=x={x}:y={y}"
        guard = enable_expression(layer.start, layer.end, duration)
        if guard:
            overlay += f":enable='{guard}'"
        audio_chains.append(f"{overlay}[vimg{offset}]")
        video_label = f"[vimg{offset}]"

    wave_layer = parsed.waveform_layer()
    if wave_layer is not None and parsed.wave_style in ENVELOPE_STYLES:
        video_label = (
            _envelope_wave(
                audio_chains, video_label, parsed, wave_layer,
                width, height, duration, peaks or [],
            )
            or video_label
        )
    elif wave_layer is not None and parsed.wave_style != "none":
        mode, bar_width, height_scale = WAVE_STYLES[parsed.wave_style]
        wave_x, wave_y, wave_width, wave_height = wave_layer.pixels(width, height)
        wave_height = max(40, int(wave_height * height_scale))
        # Draw into a narrow buffer and scale it back up with nearest-neighbour
        # so each column becomes a solid bar; at bar_width 1 this is a no-op.
        draw_width = max(16, wave_width // bar_width)
        upscale = (
            f",scale={wave_width}:{wave_height}:flags=neighbor" if bar_width > 1 else ""
        )
        # The waveform always tracks speech, never the music bed, so the visual
        # stays locked to what the listener is following.
        audio_chains.append(
            # dynaudnorm normalises only this branch. Conversational speech sits
            # far below peak, so an untouched signal draws a thin line barely
            # off the centre axis; lifting it fills the box the way an audiogram
            # is expected to look. The exported audio comes from [voice] and is
            # never touched by this.
            f"[wavesrc]dynaudnorm=framelen=200:gausssize=11:peak=0.95,"
            f"showwaves=s={draw_width}x{wave_height}:mode={mode}:"
            # draw=full paints each drawn sample at full intensity; the default
            # dims by amplitude, which the nearest-neighbour upscale then
            # smears into a washed-out band.
            f"draw=full:scale={parsed.wave_scale}:"
            f"colors={showwaves_colors(wave_layer.paint(parsed.accent))}"
            f"{upscale},format=rgba[waves]"
        )
        overlay = f"{video_label}[waves]overlay=x={wave_x}:y={wave_y}"
        guard = enable_expression(wave_layer.start, wave_layer.end, duration)
        if guard:
            overlay += f":enable='{guard}'"
        audio_chains.append(f"{overlay}[vwave]")
        video_label = "[vwave]"
    if "showwaves" not in ";".join(audio_chains):
        # showwaves is the only consumer of that split branch; without it the
        # graph has a dangling output and FFmpeg refuses to run.
        audio_chains.append("[wavesrc]anullsink")

    progress_chain = _progress_filter(parsed, width, height, duration)
    text_chain = _text_filters(
        parsed, width, height, duration, font_file if font_file is not None else find_font_file()
    )
    audio_chains.append(f"{video_label}ass=captions.ass{progress_chain}{text_chain}[v]")

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if settings.ffmpeg_threads > 0:
        # Only set when asked. FFmpeg's own default is the core count, and
        # capping it is what you want when several renders share a box: four
        # jobs each grabbing 64 threads is worse than four taking a slice.
        command += [
            "-threads", str(settings.ffmpeg_threads),
            "-filter_complex_threads", str(settings.ffmpeg_threads),
        ]
    command += [
        "-ss",
        f"{clip_start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
        "-f",
        "lavfi",
        "-i",
        f"color=c={ffmpeg_color(parsed.background)}:s={width}x{height}:r=30",
    ]
    if has_music:
        if bed.loop:
            command += ["-stream_loop", "-1"]
        command += ["-i", str(music_path)]
    # -loop 1 turns a still into a stream the overlay can span the clip with.
    if background_path is not None:
        command += ["-loop", "1", "-i", str(background_path)]
    for layer in image_layers:
        command += ["-loop", "1", "-i", str(plates.for_layer(layer.id))]

    command += [
        "-filter_complex",
        ";".join(audio_chains),
        "-map",
        "[v]",
        "-map",
        audio_label,
        "-t",
        f"{duration:.3f}",
    ]
    command += (encoder or select_encoder()).output_args(gpu_index)
    command += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Social platforms re-encode anything they receive; faststart at least
        # means the file plays while it is still being fetched.
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return command


def _peak_bars(buckets: list[float]) -> set[int]:
    """Which bars get the peak colour.

    Two conditions, and both matter. A bar must rank in the loudest slice of the
    waveform, and it must stand above one of its neighbours. The ranking keeps
    the gold sparse on compressed audio, where a plain threshold against the
    loudest moment would light up almost everything; the neighbour test stops a
    constant tone, where every bar ties, from turning an arbitrary run of bars
    gold.
    """
    if len(buckets) < 3:
        return set()
    ranked = sorted(buckets, reverse=True)
    cutoff = ranked[max(0, int(len(buckets) * PEAK_SHARE) - 1)]
    peaks = set()
    for index, value in enumerate(buckets):
        if value < cutoff:
            continue
        previous = buckets[index - 1] if index else value
        following = buckets[index + 1] if index + 1 < len(buckets) else value
        if value > previous or value > following:
            peaks.add(index)
    return peaks


def _envelope_wave(
    chains: list[str],
    video_label: str,
    parsed: Scene,
    layer: "RenderLayer",
    width: int,
    height: int,
    duration: float,
    peaks: list[float],
) -> str | None:
    """Draw the waveform from the clip's own peak envelope.

    One drawbox per bar, mirrored around the layer's centre line, then a single
    translucent box that retreats across the clip so the played part of the
    waveform stays lit. That progress reveal is one filter rather than one per
    bar, which keeps the graph small enough to stay readable.
    """
    bar_count, gap_ratio, _ = ENVELOPE_STYLES[parsed.wave_style]
    if not peaks:
        return None

    x, y, box_width, box_height = layer.pixels(width, height)
    pitch = box_width / bar_count
    bar_width = max(2, int(round(pitch * (1 - gap_ratio))))
    centre = y + box_height / 2
    colour = ffmpeg_color(layer.paint(parsed.accent))

    # Re-bucket whatever resolution we were handed to the bar count, keeping
    # peaks rather than averaging so transients survive.
    buckets = []
    step = len(peaks) / bar_count
    for index in range(bar_count):
        chunk = peaks[int(index * step) : max(int((index + 1) * step), int(index * step) + 1)]
        buckets.append(max(chunk) if chunk else 0.0)

    loudest = max(buckets) or 1.0
    span = max(0.001, layer.end - layer.start)
    # The brand's equalizer style tips the loudest bars into Champagne Gold.
    # Colour is chosen per bar when the graph is built, so this costs nothing:
    # the filter count is identical either way.
    peak_colour = (
        ffmpeg_color(layer.paint(parsed.peak_accent)) if parsed.peak_accent else colour
    )
    peaks_set = _peak_bars(buckets) if parsed.peak_accent else set()
    geometry = []
    for index, value in enumerate(buckets):
        # Normalise against the clip's own loudest moment so the waveform fills
        # its box regardless of how quietly the episode was mastered. The
        # exponent lifts quiet syllables without flattening the loud ones.
        amplitude = (value / loudest) ** 0.65
        bar_height = max(3, int(round(amplitude * box_height)))
        geometry.append(
            (
                int(round(x + index * pitch)),
                int(round(centre - bar_height / 2)),
                bar_height,
                layer.start + (index / bar_count) * span,
                peak_colour if index in peaks_set else colour,
            )
        )

    guard = enable_expression(layer.start, layer.end, duration)
    window = f":enable='{guard}'" if guard else ""

    # Two passes rather than one dimming rectangle over the top. A rectangle
    # would need `eval=frame` (drawbox resolves expressions once at init
    # otherwise) and, over a photographic background, reads as a grey block
    # rather than as unplayed audio. Lighting each bar as the playhead reaches
    # it uses `enable`, which is evaluated per frame by definition.
    # The unplayed track stays a single colour: dimmed to 30% the gold and the
    # blue are hard to tell apart anyway, and a two-tone ghost reads as noise.
    track = "".join(
        f",drawbox=x={bar_x}:y={bar_y}:w={bar_width}:h={bar_height}:"
        f"color={colour}@0.30:t=fill{window}"
        for bar_x, bar_y, bar_height, _, _ in geometry
    )
    played = "".join(
        f",drawbox=x={bar_x}:y={bar_y}:w={bar_width}:h={bar_height}:"
        f"color={bar_colour}@1.0:t=fill:enable='gte(t,{at:.3f})'"
        for bar_x, bar_y, bar_height, at, bar_colour in geometry
    )

    # The chain is built with leading commas so the halves concatenate; a label
    # must be followed directly by a filter name, so the first one goes.
    chains.append(f"{video_label}{(track + played).lstrip(',')}[vwave]")
    return "[vwave]"


# How finely the progress bar is segmented. Enough that the growth reads as
# smooth at any sane bar width, few enough to keep the filter graph small.
PROGRESS_SEGMENTS = 96


def _progress_filter(parsed: Scene, width: int, height: int, duration: float) -> str:
    """A bar that fills across the clip.

    Completion is what the platforms reward, and a visible finish line is the
    cheapest way to earn it: the viewer can see the clip is nearly over.

    Built from fixed segments gated by `enable` rather than from a single box
    whose width is an expression of `t`. drawbox only re-evaluates geometry per
    frame on newer FFmpeg — on the 5.x that Debian ships, an expression is
    resolved once at initialisation and the bar would sit frozen. Timeline
    `enable` support is old and universal, so this renders the same everywhere.
    """
    layer = parsed.progress_layer()
    if layer is None:
        return ""
    x, y, box_width, box_height = layer.pixels(width, height)
    track = ffmpeg_color(parsed.background)
    fill = ffmpeg_color(layer.paint(parsed.accent))
    span = max(0.001, layer.end - layer.start)

    guard = enable_expression(layer.start, layer.end, duration)
    window = f":enable='{guard}'" if guard else ""
    chain = (
        f",drawbox=x={x}:y={y}:w={box_width}:h={box_height}:"
        f"color={track}@0.45:t=fill{window}"
    )

    segment = box_width / PROGRESS_SEGMENTS
    for index in range(PROGRESS_SEGMENTS):
        at = layer.start + (index / PROGRESS_SEGMENTS) * span
        left = int(round(x + index * segment))
        # Overlap by a pixel so rounding never leaves a seam between segments.
        seg_width = max(1, int(round(x + (index + 1) * segment)) - left + 1)
        chain += (
            f",drawbox=x={left}:y={y}:w={seg_width}:h={box_height}:"
            f"color={fill}@1.0:t=fill:enable='gte(t,{at:.3f})'"
        )
    return chain


def _text_filters(
    parsed: Scene,
    width: int,
    height: int,
    duration: float,
    font_file: Path | None,
) -> str:
    """drawtext filters for the scene's text layers, in stacking order.

    Captions from the transcript are burned in by the ASS subtitle filter; these
    are the standalone text elements the editor lets you place on the canvas,
    which previously appeared in the preview and then vanished from the export.
    """
    if font_file is None:
        # Without a font, drawtext aborts the whole encode; dropping the text
        # layers still produces a usable audiogram.
        return ""
    chain = ""
    for layer in parsed.text_layers():
        if layer.type == "captions":
            # The transcript already drives these through captions.ass.
            continue
        x, y, box_width, box_height = layer.pixels(width, height)
        font_size = max(12, int(box_height * 0.62))
        options = [
            f"text='{escape_drawtext(layer.text)}'",
            f"fontcolor={ffmpeg_color(layer.paint('#ffffff'))}",
            f"fontsize={font_size}",
            # Centre the text inside the box the editor drew, matching how the
            # canvas preview lays the layer out.
            f"x={x}+({box_width}-text_w)/2",
            f"y={y}+({box_height}-text_h)/2",
            # Pull a too-wide line back inside the frame rather than clipping it.
            "fix_bounds=1",
        ]
        options.append(f"fontfile='{escape_drawtext(str(font_file))}'")
        guard = enable_expression(layer.start, layer.end, duration)
        if guard:
            options.append(f"enable='{guard}'")
        chain += ",drawtext=" + ":".join(options)
    return chain


def _render_audiogram_mp4(
    source_path: Path,
    output_path: Path,
    ass_path: Path,
    aspect_ratio: str,
    clip_start: float,
    duration: float,
    bed: MusicBed | None = None,
    music_path: Path | None = None,
    scene: dict | None = None,
    image_paths: dict[str, Path] | None = None,
    peaks: list[float] | None = None,
    gpu_index: str | None = None,
    plates: Plates | None = None,
    job_id: str | None = None,
) -> None:
    # Measure the mix first so the encode can be delivered at a known loudness.
    # A failed measurement is not a failed render: the clip simply goes out at
    # the level of its source, which is what happened before this existed.
    measurement = measure_loudness(
        source_path=source_path,
        clip_start=clip_start,
        duration=duration,
        bed=bed,
        music_path=music_path,
        job_id=job_id,
        scene=scene,
    )
    completed = cancellation.run(
        job_id,
        build_render_command(
            source_path=source_path,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            clip_start=clip_start,
            duration=duration,
            bed=bed,
            music_path=music_path,
            scene=scene,
            image_paths=image_paths,
            peaks=peaks,
            gpu_index=gpu_index,
            plates=plates,
            loudness_measurement=measurement,
        ),
        cwd=ass_path.parent,
        capture_output=True,
        timeout=max(60, int(duration * 8)),
    )
    # `cancellation.run` cannot use check=True, so the failure is raised here.
    # Keeping FFmpeg's own words matters: its last line is usually a generic
    # summary and the cause is a line or two above it.
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"FFmpeg failed (exit {completed.returncode}): {detail or 'no output'}"
        )


def _dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "16:9":
        return 1280, 720
    if aspect_ratio == "1:1":
        return 1080, 1080
    if aspect_ratio == "4:5":
        return 1080, 1350
    return 1080, 1920


def _clip_captions(
    transcript: dict, start: float, end: float, max_chars: int | None = None
) -> list[dict]:
    # A real transcript carries word timings, which let captions break on the
    # word rather than on the sentence — the difference between a readable
    # two-line caption and a paragraph nobody can follow at speed.
    if any(segment.get("words") for segment in transcript.get("segments", [])):
        lines = caption_lines(transcript, start, end, max_chars=max_chars)
        if lines:
            return lines

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
    return [{"start": 0.0, "end": max(0.5, end - start), "text": "Rendered locally with Kinder."}]


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


def _write_ass(
    path: Path,
    captions: list[dict],
    aspect_ratio: str,
    parsed: Scene | None = None,
    font_name: str = "DejaVu Sans",
) -> None:
    """Burn-in caption styling.

    Most of a social audience watches with the sound off, so captions carry the
    clip rather than decorate it. The preset decides how loud they look and,
    critically, how far above the bottom edge they sit: every vertical platform
    covers the lower fifth of the frame with its own interface, and a caption
    placed down there is simply not read.
    """
    width, height = _dimensions(aspect_ratio)
    preset = CAPTION_PRESETS[parsed.caption_preset if parsed else "social"]
    colour = parsed.caption_color if parsed else "#ffffff"

    # Width, not height — see CAPTION_PRESETS for why.
    font_size = max(18, int(width * preset["size_ratio"]))
    margin_v = int(height * preset["margin_ratio"])
    margin_h = int(width * 0.08)
    bold = -1 if preset["bold"] else 0

    # BorderStyle 3 paints a filled plate behind the text; 1 is outline only.
    # In BorderStyle 3 libass fills that plate with the *outline* colour, which
    # is why a coloured plate is set there rather than in BackColour.
    plate = preset.get("plate")
    if plate:
        # Inverted: dark type on a brand-coloured plate. The shadow takes the
        # plate colour too, so no dark fringe shows at the box edge.
        border_style = 3
        primary = ass_color(preset.get("plate_text", "#000000"))
        outline = ass_color(plate)
        back = ass_color(plate)
    else:
        border_style = 3 if preset["back_alpha"] != "00" else 1
        primary = ass_color(colour)
        outline = ass_color("#000000")
        back = ass_color("#000000", preset["back_alpha"])

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},{primary},{primary},"
        f"{outline},{back},{bold},0,0,0,100,100,0,0,{border_style},"
        f"{preset['outline']},{preset['shadow']},2,{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    highlight = preset.get("highlight") if (parsed is None or parsed.word_highlight) else None

    # Attribution only appears when there is something to attribute. A clip with
    # one voice renders exactly as it did before this existed: no tint, no
    # prefix. The feature has to cost nothing when it is not being used.
    speakers_present = {
        caption.get("speaker_id") for caption in captions if caption.get("speaker_id")
    }
    tint = len(speakers_present) > 1

    for caption in captions:
        upper = preset["uppercase"]
        words = caption.get("words") or []
        # On a plated preset the plate is already the accent colour, so tinting
        # the type as well would put a colour on a colour; the plate carries the
        # attribution instead and the text stays legible.
        voice = (
            speaker_colour(int(caption["speaker_id"]))
            if tint and caption.get("speaker_id") and not preset.get("plate")
            else None
        )
        if highlight and len(words) > 1:
            lines.extend(_karaoke_events(caption, words, highlight, upper, voice))
            continue
        text = caption["text"].upper() if upper else caption["text"]
        body = _ass_escape(text)
        if voice:
            body = "{\\c" + ass_color(voice) + "}" + body
        lines.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(caption['start'])},"
            f"{_ass_timestamp(caption['end'])},"
            f"Default,,0,0,0,,{body}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _karaoke_events(
    caption: dict,
    words: list[dict],
    highlight: str,
    upper: bool,
    voice: str | None = None,
) -> list[str]:
    r"""One subtitle event per word, with the spoken word recoloured.

    Word-by-word highlighting is what a social caption looks like now, and it is
    the most recognisable thing about a Headliner clip.

    ASS has a `\k` karaoke tag, but libass drives it from the style's secondary
    colour and sweeps through the word rather than switching at its boundary,
    which reads as a smear at these sizes. Emitting one event per word and
    recolouring inline switches exactly on the boundary. The cost is a subtitle
    line per word — a few hundred for a clip, which is nothing to render.
    """
    colour = ass_color(highlight)
    events: list[str] = []
    for index, word in enumerate(words):
        start = float(word.get("start", caption["start"]))
        # Each word holds until the next one starts, so there is no gap where
        # nothing is lit. The last one holds to the end of the line, which stops
        # the caption blinking out early on a trailing pause.
        if index + 1 < len(words):
            end = float(words[index + 1].get("start", word.get("end", start)))
        else:
            end = float(caption["end"])
        if end <= start:
            continue

        rendered = []
        for position, other in enumerate(words):
            token = str(other.get("text", ""))
            if upper:
                token = token.upper()
            token = _ass_escape(token)
            # `\c` with no argument restores the style colour, so only the word
            # being spoken is recoloured.
            if position == index:
                rendered.append("{\\c" + colour + "}" + token + "{\\c}")
            elif voice:
                # The rest of the line keeps the speaker's colour, so the
                # highlight reads against the attribution rather than erasing it.
                rendered.append("{\\c" + ass_color(voice) + "}" + token + "{\\c}")
            else:
                rendered.append(token)
        events.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(start)},"
            f"{_ass_timestamp(end)},"
            f"Default,,0,0,0,,{' '.join(rendered)}"
        )
    return events


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

