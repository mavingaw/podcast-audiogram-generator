from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    """A client on its own database, config directory and data directory.

    **Import app modules only after calling this.** It reloads the config, db
    and service modules so each test gets a fresh database, which means any name
    bound beforehand — `SessionLocal`, `settings`, a model class, a service
    function — still points at the *previous* test's database. A seed written
    through a stale `SessionLocal` lands somewhere the app will never read, and
    the symptom is a confusing 404 rather than an error. This has cost three
    debugging sessions; bind late.
    """
    monkeypatch.setenv("PAS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'config' / 'test.db'}")
    # Tests drive the queue themselves. Leaving the real workers running means
    # three threads racing the test for the same rows, which is a flake, not a
    # finding — jobs the tests want to run are invoked directly.
    monkeypatch.setenv("PAS_RUN_WORKER", "false")

    import app.core.config as config
    import app.db.session as session
    import app.db.models as models
    import app.db.init_db as init_db
    import app.api.deps as deps
    import app.services.auth as auth
    import app.services.rss as rss
    import app.services.storage as storage
    import app.services.chunked_upload as chunked_upload
    import app.services.preview as preview
    import app.services.waveform as waveform_service
    import app.services.scene as scene_service
    import app.services.library as library
    import app.services.music_bed as music_bed
    import app.services.batching as batching
    import app.services.jobs as jobs
    import app.api.routes as routes
    import app.main as main

    importlib.reload(config)
    importlib.reload(session)
    importlib.reload(models)
    importlib.reload(init_db)
    importlib.reload(deps)
    importlib.reload(auth)
    importlib.reload(rss)
    importlib.reload(storage)
    # After storage, which it takes its exception type from: a stale copy
    # raises an error class the route no longer recognises, and a refusal
    # that should be a 415 comes out as a 500.
    importlib.reload(chunked_upload)
    importlib.reload(preview)
    importlib.reload(waveform_service)
    importlib.reload(scene_service)
    importlib.reload(library)
    importlib.reload(music_bed)
    # Reloaded before routes: it binds model classes at import time, so a
    # stale copy leaves two registries and mappers stop resolving.
    importlib.reload(batching)
    importlib.reload(jobs)
    importlib.reload(routes)
    importlib.reload(main)

    return TestClient(main.create_app())


def register_second_user(client, username: str, password: str = "another-password"):
    """Create and sign in as a second account.

    Registration is closed by default — a public sign-up form on a reachable
    address means anybody with the URL has an account — so tests that need
    somebody else open it first. What they are exercising is ownership, not the
    registration policy; `test_auth_hardening.py` covers that.
    """
    # Written straight to the setting rather than through the admin endpoint:
    # these helpers are usually called *after* signing out, so there is no admin
    # session left to change it with.
    from app.api.routes import SIGNUP_SETTING
    from app.db.models import AppSetting
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.merge(AppSetting(key=SIGNUP_SETTING, value="true"))
        db.commit()

    return client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )


def test_bootstrap_and_project_flow(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    assert client.get("/api/bootstrap").json() == {"initialized": False}

    response = client.post("/api/bootstrap", json={"username": "admin", "password": "long-password"})
    assert response.status_code == 200
    assert response.json()["user"]["is_admin"] is True

    project_response = client.post("/api/projects", json={"title": "Episode clip"})
    assert project_response.status_code == 200
    project = project_response.json()["project"]
    assert project["title"] == "Episode clip"

    render_response = client.post(f"/api/projects/{project['id']}/render")
    assert render_response.status_code == 200
    assert render_response.json()["job"]["kind"] == "render"


def test_upload_rejects_unsupported_file_type(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "admin", "password": "long-password"})

    response = client.post(
        "/api/media/upload",
        files={"file": ("notes.txt", b"not media", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Upload must be an audio or video file"


def test_transcript_updates_are_persisted(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "admin", "password": "long-password"})
    upload = client.post(
        "/api/media/upload",
        files={"file": ("episode.mp3", b"fake mp3", "audio/mpeg")},
    )
    media_id = upload.json()["media"]["id"]
    transcript = {
        "language": "en",
        "duration": 3,
        "segments": [{"id": 1, "speaker": "Host", "start": 0, "end": 3, "text": "Edited line"}],
    }

    response = client.patch(f"/api/media/{media_id}/transcript", json={"transcript": transcript})

    assert response.status_code == 200
    assert response.json()["media"]["transcript"]["segments"][0]["text"] == "Edited line"


def test_rss_preview_blocks_local_addresses(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "admin", "password": "long-password"})

    response = client.post("/api/rss/preview", json={"url": "http://127.0.0.1/feed.xml"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Feed host resolves to a private or local address"



# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


def test_a_session_expires_and_the_token_stops_working(monkeypatch, tmp_path):
    """Tokens used to be immortal: one issued once stayed valid forever."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})
    assert client.get("/api/me").status_code == 200

    import datetime as dt

    import app.db.models as models
    import app.db.session as session

    with session.SessionLocal() as db:
        token = db.query(models.SessionToken).first()
        # Older than any sane lifetime.
        token.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
        db.commit()

    assert client.get("/api/me").status_code == 401


def test_an_expired_token_is_deleted_rather_than_left_to_rot(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    import datetime as dt

    import app.db.models as models
    import app.db.session as session

    with session.SessionLocal() as db:
        db.query(models.SessionToken).first().created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
        )
        db.commit()

    client.get("/api/me")
    with session.SessionLocal() as db:
        assert db.query(models.SessionToken).count() == 0


def test_signing_in_purges_other_expired_sessions(monkeypatch, tmp_path):
    """Otherwise the table only ever grows."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    import datetime as dt

    import app.db.models as models
    import app.db.session as session

    with session.SessionLocal() as db:
        user = db.query(models.User).first()
        stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
        for index in range(5):
            db.add(models.SessionToken(token_hash=f"stale-{index}", user_id=user.id, created_at=stale))
        db.commit()
        assert db.query(models.SessionToken).count() == 6

    client.post("/api/auth/login", json={"username": "tester", "password": "long-password"})
    with session.SessionLocal() as db:
        # The five stale rows are gone; the original and the new one remain.
        assert db.query(models.SessionToken).count() == 2


def test_a_fresh_session_survives_the_purge(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})
    client.post("/api/auth/login", json={"username": "tester", "password": "long-password"})
    assert client.get("/api/me").status_code == 200


def test_the_session_cookie_does_not_outlive_the_session(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    response = client.post(
        "/api/bootstrap", json={"username": "tester", "password": "long-password"}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "max-age=" in cookie

    import app.core.config as config

    assert f"max-age={config.settings.session_days * 24 * 60 * 60}" in cookie


# --------------------------------------------------------------------------
# Usernames and self sign-up
# --------------------------------------------------------------------------


def test_usernames_are_case_insensitive(monkeypatch, tmp_path):
    """`Mujin` and `mujin` must be one account, not two."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "Mujin", "password": "long-password"})
    assert client.get("/api/me").json()["user"]["username"] == "mujin"

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"username": "MUJIN", "password": "long-password"}
    ).status_code == 200


def test_a_bad_username_is_refused(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    for bad in ("ab", "has space", "sym$bol", "-leading", "x" * 40):
        response = client.post(
            "/api/bootstrap", json={"username": bad, "password": "long-password"}
        )
        assert response.status_code == 422, bad


def test_a_short_password_is_refused(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    assert client.post(
        "/api/bootstrap", json={"username": "mujin", "password": "short"}
    ).status_code == 422


def test_sign_up_creates_a_non_admin_and_signs_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    client.post("/api/auth/logout")

    response = register_second_user(client, "guest", "another-password")
    assert response.status_code == 200
    body = response.json()["user"]
    assert body["username"] == "guest"
    # Sign-up must never mint an administrator.
    assert body["is_admin"] is False
    # And it signs you straight in.
    assert client.get("/api/me").json()["user"]["username"] == "guest"


def test_sign_up_refuses_a_taken_username(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    assert register_second_user(client, "MUJIN", "another-password").status_code == 409


def test_sign_up_cannot_be_used_to_claim_an_empty_instance(monkeypatch, tmp_path):
    """Otherwise the first person through the door gets a non-admin account
    and nobody can ever administer the box."""
    client = create_test_client(monkeypatch, tmp_path)
    assert register_second_user(client, "guest", "another-password").status_code == 409


def test_an_admin_can_open_and_close_sign_ups(monkeypatch, tmp_path):
    """Closed by default; see test_auth_hardening.py for why."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    assert client.get("/api/auth/signup").json()["open"] is False

    client.put("/api/settings/signups", json={"open": True})
    assert client.get("/api/auth/signup").json()["open"] is True

    client.put("/api/settings/signups", json={"open": False})
    assert client.get("/api/auth/signup").json()["open"] is False
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/register", json={"username": "guest", "password": "another-password"}
    ).status_code == 403


def test_only_an_admin_can_change_the_sign_up_setting(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")

    assert client.put("/api/settings/signups", json={"open": False}).status_code == 403


def test_login_does_not_reveal_whether_a_username_exists(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    missing = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever123"})
    wrong = client.post("/api/auth/login", json={"username": "mujin", "password": "whatever123"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_existing_email_accounts_migrate_to_usernames(monkeypatch, tmp_path):
    """An install predating this change must keep working, signed in as the
    local part of whatever address it had."""
    import sqlalchemy as sa

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})

    import app.db.init_db as init_db
    import app.db.session as session

    # Put the schema back the way it was, with an address in it.
    with session.engine.begin() as connection:
        connection.execute(sa.text('ALTER TABLE users RENAME COLUMN username TO email'))
        connection.execute(sa.text("UPDATE users SET email = 'mujin@example.com'"))

    init_db.rename_email_to_username()

    with session.engine.begin() as connection:
        names = [row[0] for row in connection.execute(sa.text("SELECT username FROM users"))]
    assert names == ["mujin"]


# --------------------------------------------------------------------------
# Deleting a project
# --------------------------------------------------------------------------


def test_a_project_can_be_deleted(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Throwaway"}).json()["project"]

    # Delete is two-step now: the first goes to the trash, the second is real.
    assert client.delete(f"/api/projects/{project['id']}").json()["trashed"] is True
    assert client.get("/api/projects").json()["projects"] == []
    assert client.delete(f"/api/projects/{project['id']}").json()["trashed"] is False
    # And it stays gone.
    assert client.delete(f"/api/projects/{project['id']}").status_code == 404


def test_deleting_a_project_takes_its_jobs_with_it(monkeypatch, tmp_path):
    """A render left queued against a deleted project would write output for
    something that no longer exists."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Throwaway"}).json()["project"]
    client.post(f"/api/projects/{project['id']}/render")

    client.delete(f"/api/projects/{project['id']}", params={"forever": "1"})
    remaining = [
        job for job in client.get("/api/jobs").json()["jobs"]
        if job["subject_id"] == project["id"]
    ]
    assert remaining == []


def test_deleting_a_project_removes_its_outputs(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    # The fixture reloads the config module, so read the settings the route
    # itself is bound to rather than a separately imported copy.
    from app.api.routes import settings

    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Throwaway"}).json()["project"]

    outputs = settings.outputs_dir / project["id"]
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "audiogram.mp4").write_bytes(b"not really a video")

    client.delete(f"/api/projects/{project['id']}", params={"forever": "1"})
    assert not outputs.exists()


def test_someone_elses_project_cannot_be_deleted(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Mine"}).json()["project"]

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 404

    # And it really is still there for its owner.
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "mujin", "password": "long-password"})
    assert len(client.get("/api/projects").json()["projects"]) == 1


# --------------------------------------------------------------------------
# Additive migration
# --------------------------------------------------------------------------


def test_a_column_that_already_exists_is_still_repaired(monkeypatch, tmp_path):
    """The backfill has to run every start, not only when a column is added.

    A column added by an earlier version that did not backfill leaves its rows
    NULL forever otherwise — which is exactly what happened on the live
    database, where sixteen projects came back with no review state.

    Note the column can only *be* NULL when it arrived through ALTER TABLE:
    `create_all` makes it NOT NULL on a fresh database.
    """
    from sqlalchemy import text

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Older"}).json()["project"]

    import app.db.init_db as init_db
    from app.db.session import engine

    # Recreate the state an older version left behind: the column exists,
    # nullable, and its rows were never filled in.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE projects DROP COLUMN review_state"))
    init_db.add_missing_columns()
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT review_state FROM projects LIMIT 1")
        ).scalar() is None

    init_db.backfill_defaults()

    body = client.get("/api/projects").json()["projects"]
    restored = next(item for item in body if item["id"] == project["id"])
    assert restored["review_state"] == "approved"


def test_a_new_column_is_backfilled_on_existing_rows(monkeypatch, tmp_path):
    """The same repair, through the start-up path rather than in isolation."""
    from sqlalchemy import text

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    project = client.post("/api/projects", json={"title": "Older"}).json()["project"]

    import app.db.init_db as init_db
    from app.db.session import engine

    # Put the database back to how it looked before the column existed.
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE projects DROP COLUMN review_state'))
    init_db.init_db()

    body = client.get(f"/api/projects").json()["projects"]
    restored = next(item for item in body if item["id"] == project["id"])
    assert restored["review_state"] == "approved", "existing rows were left as NULL"
