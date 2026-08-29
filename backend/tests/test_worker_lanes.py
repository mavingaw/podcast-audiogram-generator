from __future__ import annotations

import dataclasses
import threading

from tests.test_api import create_test_client, register_second_user


def seed(models, session, kinds: list[str], owner_id: str) -> list[str]:
    ids = []
    with session.SessionLocal() as db:
        for kind in kinds:
            job = models.Job(
                owner_id=owner_id,
                kind=models.JobKind(kind),
                status=models.JobStatus.queued,
                message="queued",
            )
            db.add(job)
            db.flush()
            ids.append(job.id)
        db.commit()
    return ids


def owner(models, session) -> str:
    with session.SessionLocal() as db:
        return db.query(models.User).first().id


def bootstrap(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})
    import app.db.models as models
    import app.db.session as session
    import app.services.jobs as jobs

    return client, models, session, jobs


def test_lanes_cover_every_job_kind(monkeypatch, tmp_path):
    """A kind missing from every lane would queue forever with no worker."""
    _, models, _, jobs = bootstrap(monkeypatch, tmp_path)
    covered = {kind for kinds in jobs.LANES.values() for kind in kinds}
    assert covered == set(models.JobKind)


def test_every_kind_has_a_handler(monkeypatch, tmp_path):
    _, models, _, jobs = bootstrap(monkeypatch, tmp_path)
    assert set(jobs._HANDLERS) == set(models.JobKind)


def test_render_and_transcribe_are_in_separate_lanes(monkeypatch, tmp_path):
    """The whole point: a long render must not block a transcribe."""
    _, models, _, jobs = bootstrap(monkeypatch, tmp_path)
    lane_of = {
        kind: lane for lane, kinds in jobs.LANES.items() for kind in kinds
    }
    assert lane_of[models.JobKind.render] != lane_of[models.JobKind.transcribe]
    assert lane_of[models.JobKind.analyze_media] != lane_of[models.JobKind.render]


def test_a_lane_only_claims_its_own_kinds(monkeypatch, tmp_path):
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    seed(models, session, ["render"], user_id)

    # The transcribe lane must not touch a queued render.
    assert jobs._claim_job(jobs.LANES["transcribe"]) is None
    claimed = jobs._claim_job(jobs.LANES["render"])
    assert claimed is not None


def test_claiming_is_oldest_first(monkeypatch, tmp_path):
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    first, second = seed(models, session, ["waveform", "waveform"], user_id)

    assert jobs._claim_job(jobs.LANES["media"]) == first
    assert jobs._claim_job(jobs.LANES["media"]) == second


def test_a_claimed_job_is_not_handed_out_twice(monkeypatch, tmp_path):
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    seed(models, session, ["waveform"], user_id)

    assert jobs._claim_job(jobs.LANES["media"]) is not None
    # It is running now, so a second look finds nothing.
    assert jobs._claim_job(jobs.LANES["media"]) is None


def test_concurrent_claims_never_hand_out_the_same_job(monkeypatch, tmp_path):
    """Several lanes poll the same table; two must not win the same row.

    The claim is a conditional UPDATE precisely so that a read-then-write race
    cannot double-dispatch a job — which would run a render twice and corrupt
    its output directory.
    """
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    total = 12
    seed(models, session, ["waveform"] * total, user_id)

    claimed: list[str] = []
    guard = threading.Lock()
    start = threading.Barrier(6)

    def worker():
        start.wait()
        while True:
            job_id = jobs._claim_job(jobs.LANES["media"])
            if job_id is None:
                return
            with guard:
                claimed.append(job_id)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(claimed) == total, f"claimed {len(claimed)} of {total}"
    assert len(set(claimed)) == total, "a job was claimed by more than one worker"


def test_interrupted_jobs_are_requeued_before_workers_start(monkeypatch, tmp_path):
    """A restart mid-job must not leave it 'running' with nobody working on it."""
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    with session.SessionLocal() as db:
        db.get(models.Job, job_id).status = models.JobStatus.running
        db.commit()

    jobs._requeue_interrupted_jobs()

    with session.SessionLocal() as db:
        job = db.get(models.Job, job_id)
        assert job.status == models.JobStatus.queued
        assert "restart" in job.message.lower()


# --------------------------------------------------------------------------
# Concurrent renders
# --------------------------------------------------------------------------


def test_lane_width_scales_with_the_machine(monkeypatch, tmp_path):
    """One render uses about four threads, so a big box wants more renders."""
    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)

    monkeypatch.setattr(jobs.os, "cpu_count", lambda: 64)
    # Settings is frozen, so swap in a copy rather than mutating it.
    monkeypatch.setattr(jobs, "settings", dataclasses.replace(jobs.settings, render_workers=0))
    assert jobs.lane_workers("render") == 4

    monkeypatch.setattr(jobs.os, "cpu_count", lambda: 4)
    assert jobs.lane_workers("render") == 1


def test_lane_width_can_be_pinned(monkeypatch, tmp_path):
    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs, "settings", dataclasses.replace(jobs.settings, render_workers=2))
    assert jobs.lane_workers("render") == 2


def test_transcription_stays_single_lane(monkeypatch, tmp_path):
    """A second worker means a second model in VRAM for no throughput."""
    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)
    monkeypatch.setattr(jobs.os, "cpu_count", lambda: 64)
    assert jobs.lane_workers("transcribe") == 1


def test_publishing_replaces_outputs_and_leaves_scratch_to_the_caller(monkeypatch, tmp_path):
    """Publishing moves the outputs; the render's `finally` owns the cleanup,
    so it happens on a cancelled or failed run too."""
    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)

    work = tmp_path / "work"
    out = tmp_path / "out"
    work.mkdir()
    out.mkdir()
    (out / "audiogram.mp4").write_text("stale")
    (work / "audiogram.mp4").write_text("fresh")
    (work / "captions.srt").write_text("subs")
    # Plates are scratch, not output.
    (work / "plate-background.png").write_text("plate")

    jobs._publish_render(work, out)

    assert (out / "audiogram.mp4").read_text() == "fresh"
    assert (out / "captions.srt").read_text() == "subs"
    # Plates are scratch, not output, so they are not published.
    assert not (out / "plate-background.png").exists()
    assert (work / "plate-background.png").exists()


def test_two_renders_of_one_project_cannot_interleave(monkeypatch, tmp_path):
    """They share an output path, so the second must wait for the first."""
    import threading
    import time

    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)

    inside: list[str] = []
    overlapped = threading.Event()

    def hold(name: str):
        with jobs._project_lock("same-project"):
            inside.append(name)
            if len(inside) > 1:
                overlapped.set()
            time.sleep(0.3)
            inside.remove(name)

    threads = [threading.Thread(target=hold, args=(f"r{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not overlapped.is_set(), "two renders held the same project at once"


def test_different_projects_render_in_parallel(monkeypatch, tmp_path):
    """The lock is per project; unrelated renders must not queue behind it."""
    import threading

    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)

    first = jobs._project_lock("project-a")
    first.acquire()
    try:
        # A different project's lock is a different lock, so this is free.
        assert jobs._project_lock("project-b").acquire(timeout=2)
        jobs._project_lock("project-b").release()
    finally:
        first.release()


# --------------------------------------------------------------------------
# Clip range against the source
# --------------------------------------------------------------------------


def project_with_clip(models, session, media_seconds, start, end):
    """A project whose clip range may or may not fit inside its source."""
    with session.SessionLocal() as db:
        user = db.query(models.User).first()
        media = models.MediaAsset(
            owner_id=user.id,
            original_name="source.wav",
            stored_name="source.wav",
            content_type="audio/wav",
            size_bytes=1,
            duration_seconds=media_seconds,
        )
        db.add(media)
        db.commit()
        project = models.Project(
            owner_id=user.id,
            media_id=media.id,
            title="clip range",
            clip_start=start,
            clip_end=end,
            aspect_ratio="9:16",
            scene_json="{}",
        )
        db.add(project)
        db.commit()
        job = models.Job(
            owner_id=user.id,
            kind=models.JobKind.render,
            subject_id=project.id,
            status=models.JobStatus.running,
        )
        db.add(job)
        db.commit()
        return project.id, job.id


def test_a_clip_starting_past_the_source_fails_loudly(monkeypatch, tmp_path):
    """It used to render a frozen frame over silence and call that success."""
    import pytest

    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    import app.core.config as config

    (config.settings.uploads_dir / "source.wav").write_bytes(b"")
    _, job_id = project_with_clip(models, session, media_seconds=6.0, start=10.0, end=16.0)

    with session.SessionLocal() as db:
        job = db.get(models.Job, job_id)
        with pytest.raises(RuntimeError, match="only 6.0s long"):
            jobs._render(db, job)


def test_a_clip_running_past_the_end_is_shortened(monkeypatch, tmp_path):
    """Rendering silence past the end is not a useful clip; trim to the source."""
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    import app.core.config as config

    (config.settings.uploads_dir / "source.wav").write_bytes(b"")
    _, job_id = project_with_clip(models, session, media_seconds=6.0, start=2.0, end=30.0)

    seen: dict[str, float] = {}

    def capture(**kwargs):
        seen["duration"] = kwargs["duration"]
        raise RuntimeError("stop here; the clamp is what is under test")

    monkeypatch.setattr(jobs, "_render_audiogram_mp4", capture)

    with session.SessionLocal() as db:
        job = db.get(models.Job, job_id)
        try:
            jobs._render(db, job)
        except RuntimeError:
            pass

    # 2s in to a 6s source is 4s, not the 28s the project asked for.
    assert seen["duration"] == 4.0


def test_a_clip_inside_the_source_is_left_alone(monkeypatch, tmp_path):
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    import app.core.config as config

    (config.settings.uploads_dir / "source.wav").write_bytes(b"")
    _, job_id = project_with_clip(models, session, media_seconds=60.0, start=5.0, end=20.0)

    seen: dict[str, float] = {}

    def capture(**kwargs):
        seen["duration"] = kwargs["duration"]
        raise RuntimeError("stop here")

    monkeypatch.setattr(jobs, "_render_audiogram_mp4", capture)
    with session.SessionLocal() as db:
        try:
            jobs._render(db, db.get(models.Job, job_id))
        except RuntimeError:
            pass

    assert seen["duration"] == 15.0


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


def test_a_queued_job_is_cancelled_outright(monkeypatch, tmp_path):
    client, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    body = client.post(f"/api/jobs/{job_id}/cancel").json()["job"]
    assert body["status"] == "canceled"

    # And a lane must not then pick it up.
    assert jobs._claim_job(jobs.LANES["render"]) is None


def test_cancelling_a_running_job_signals_the_worker(monkeypatch, tmp_path):
    from app.services import cancellation

    client, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    with session.SessionLocal() as db:
        db.get(models.Job, job_id).status = models.JobStatus.running
        db.commit()

    client.post(f"/api/jobs/{job_id}/cancel")
    assert cancellation.is_requested(job_id)
    cancellation.clear(job_id)


def test_a_finished_job_cannot_be_cancelled(monkeypatch, tmp_path):
    client, models, session, _ = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    with session.SessionLocal() as db:
        db.get(models.Job, job_id).status = models.JobStatus.complete
        db.commit()

    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_someone_elses_job_cannot_be_cancelled(monkeypatch, tmp_path):
    client, models, session, _ = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 404


def test_a_cancelled_job_stops_at_the_next_step(monkeypatch, tmp_path):
    """The flag is checked between phases, not only inside a subprocess."""
    import pytest

    from app.services import cancellation
    from app.services.cancellation import JobCancelled

    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    user_id = owner(models, session)
    [job_id] = seed(models, session, ["render"], user_id)

    cancellation.request(job_id)
    try:
        with session.SessionLocal() as db:
            with pytest.raises(JobCancelled):
                jobs._step(db, db.get(models.Job, job_id), 20, "working")
    finally:
        cancellation.clear(job_id)


def test_a_cancel_that_arrives_first_still_kills_the_child(monkeypatch, tmp_path):
    """A cancel during process start-up must not be lost."""
    import subprocess
    import sys

    from app.services import cancellation

    bootstrap(monkeypatch, tmp_path)
    job_id = "race-check"
    cancellation.request(job_id)
    try:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        cancellation.register(job_id, process)
        assert process.wait(timeout=15) is not None
    finally:
        cancellation.clear(job_id)


def test_clearing_forgets_the_job(monkeypatch, tmp_path):
    from app.services import cancellation

    bootstrap(monkeypatch, tmp_path)
    cancellation.request("gone")
    assert cancellation.is_requested("gone")
    cancellation.clear("gone")
    assert not cancellation.is_requested("gone")


def test_a_failed_render_does_not_leave_its_scratch_behind(monkeypatch, tmp_path):
    """A killed render leaves plates and a partial MP4 nobody collects."""
    _, models, session, jobs = bootstrap(monkeypatch, tmp_path)
    import app.core.config as config

    (config.settings.uploads_dir / "source.wav").write_bytes(b"")
    _, job_id = project_with_clip(models, session, media_seconds=60.0, start=0.0, end=5.0)

    with session.SessionLocal() as db:
        try:
            jobs._render(db, db.get(models.Job, job_id))
        except Exception:
            pass  # It fails on the empty source; the cleanup is what matters.

    leftovers = list(config.settings.work_dir.glob("render-*"))
    assert leftovers == [], leftovers


def test_startup_clears_anything_left_in_the_scratch(monkeypatch, tmp_path):
    _, _, _, jobs = bootstrap(monkeypatch, tmp_path)
    import app.core.config as config

    stale = config.settings.work_dir / "render-from-a-previous-life"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "audiogram.mp4").write_text("partial")

    jobs._clear_stale_scratch()
    assert not stale.exists()
