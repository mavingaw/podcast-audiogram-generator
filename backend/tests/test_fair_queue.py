"""Sharing the queue.

Strict first-come-first-served is fine for one person and hostile the moment
friends are on the same instance: someone queueing ten clips takes every lane
and everyone else waits behind all ten.
"""

from __future__ import annotations

from app.db.models import JobKind, JobStatus
from tests.test_api import create_test_client

# `create_test_client` reloads the db and job modules onto a per-test database.
# Importing `SessionLocal` or `_claim_job` at module scope would bind to the
# copies from before that reload, so the test would write to one database and
# the code under test would read another.


def claim(kinds=(JobKind.render,)):
    from app.services.jobs import _claim_job

    return _claim_job(kinds)


def make_jobs(owner: str, count: int, kind=JobKind.render, status=JobStatus.queued):
    from app.db.models import Job, User
    from app.db.session import SessionLocal

    created = []
    with SessionLocal() as db:
        # Jobs are owned, and the ownership is a real foreign key.
        if db.get(User, owner) is None:
            db.add(User(id=owner, username=owner, password_hash="x"))
            db.flush()
        for _ in range(count):
            job = Job(owner_id=owner, kind=kind, status=status, message="q")
            db.add(job)
            db.flush()
            created.append(job.id)
        db.commit()
    return created


def owner_of(job_id: str) -> str:
    from app.db.models import Job
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return db.get(Job, job_id).owner_id


def test_a_lone_user_still_gets_fifo(monkeypatch, tmp_path):
    create_test_client(monkeypatch, tmp_path)
    first, second, third = make_jobs("solo", 3)
    assert claim() == first
    assert claim() == second
    assert claim() == third


def test_a_batch_does_not_lock_everyone_else_out(monkeypatch, tmp_path):
    """The case this exists for: one person queues ten, another queues one."""
    create_test_client(monkeypatch, tmp_path)
    make_jobs("hog", 10)
    make_jobs("guest", 1)

    # The hog is served first — they queued first and nothing is running yet.
    assert owner_of(claim()) == "hog"
    # But now they have work in flight, so the guest goes next rather than
    # waiting behind the other nine.
    assert owner_of(claim()) == "guest"


def test_owners_take_turns_as_the_lane_drains(monkeypatch, tmp_path):
    create_test_client(monkeypatch, tmp_path)
    make_jobs("a", 4)
    make_jobs("b", 4)

    picked = [owner_of(claim()) for _ in range(4)]
    # Neither owner may take three of the first four slots.
    assert picked.count("a") == 2 and picked.count("b") == 2, picked


def test_finished_work_stops_counting_against_an_owner(monkeypatch, tmp_path):
    """Only *running* jobs weigh; a completed batch is not a penalty."""
    create_test_client(monkeypatch, tmp_path)
    make_jobs("a", 5, status=JobStatus.complete)
    make_jobs("a", 1)
    make_jobs("b", 1)
    assert owner_of(claim()) == "a"


def test_other_lanes_do_not_count_against_an_owner(monkeypatch, tmp_path):
    """A transcribe in flight must not push someone down the render queue."""
    create_test_client(monkeypatch, tmp_path)
    make_jobs("a", 2, kind=JobKind.transcribe, status=JobStatus.running)
    make_jobs("a", 1)
    make_jobs("b", 1)
    assert owner_of(claim()) == "a"


def test_claiming_marks_the_job_running(monkeypatch, tmp_path):
    create_test_client(monkeypatch, tmp_path)
    job_id = make_jobs("solo", 1)[0]
    claim()
    from app.db.models import Job
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert db.get(Job, job_id).status == JobStatus.running


def test_an_empty_queue_claims_nothing(monkeypatch, tmp_path):
    create_test_client(monkeypatch, tmp_path)
    assert claim() is None


def test_a_lane_only_claims_its_own_kinds(monkeypatch, tmp_path):
    create_test_client(monkeypatch, tmp_path)
    make_jobs("a", 1, kind=JobKind.transcribe)
    assert claim() is None
