"""Watching podcast feeds.

The loop closing: an episode publishes, and by the time anybody looks there are
clips waiting. These cover the parts that must not go wrong unattended —
importing an episode twice, hammering somebody's server, or publishing anything
without being asked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import feeds
from tests.test_api import create_test_client, register_second_user


class Entry(dict):
    """feedparser returns attribute-and-key hybrids; a dict is close enough."""


def entry(guid="g1", title="An episode", url="https://example.com/ep1.mp3",
          kind="audio/mpeg", published="Mon, 01 Jan 2026 00:00:00 GMT"):
    return Entry({
        "id": guid,
        "title": title,
        "published": published,
        "enclosures": [{"href": url, "type": kind, "length": "1000"}],
        "links": [],
    })


class Parsed:
    def __init__(self, entries, status=200, etag=None, modified=None, bozo=0):
        self.entries = entries
        self.status = status
        self.etag = etag
        self.modified = modified
        self.bozo = bozo
        self.feed = type("F", (), {"title": "The Show"})()


# --------------------------------------------------------------------------
# Reading a feed
# --------------------------------------------------------------------------


def test_episodes_are_read_from_enclosures():
    found = feeds.episodes_of(Parsed([entry()]))
    assert len(found) == 1
    assert found[0].url == "https://example.com/ep1.mp3"
    assert found[0].title == "An episode"


def test_entries_without_audio_are_ignored():
    """A feed can carry blog posts alongside episodes."""
    text_only = Entry({"id": "x", "title": "A post", "enclosures": [], "links": []})
    assert feeds.episodes_of(Parsed([text_only])) == []


def test_audio_in_a_plain_link_is_still_found():
    """Not every feed uses an enclosure."""
    linked = Entry({
        "id": "x", "title": "Episode", "enclosures": [],
        "links": [{"href": "https://example.com/a.mp3", "type": "audio/mpeg"}],
    })
    assert len(feeds.episodes_of(Parsed([linked]))) == 1


def test_identity_prefers_the_feeds_own_guid():
    """Dates are unreliable — feeds backfill — and titles get typo-fixed."""
    assert feeds.episode_id(entry(guid="stable-id")) == "stable-id"


def test_identity_falls_back_to_the_file_when_there_is_no_guid():
    no_guid = Entry({
        "title": "Episode", "enclosures": [{"href": "https://x/a.mp3", "type": "audio/mpeg"}],
        "links": [],
    })
    assert feeds.episode_id(no_guid) == "https://x/a.mp3"


# --------------------------------------------------------------------------
# Being polite
# --------------------------------------------------------------------------


def test_a_feed_is_not_reread_immediately():
    assert feeds.due(datetime.now(timezone.utc)) is False


def test_a_feed_never_checked_is_due():
    assert feeds.due(None) is True


def test_a_feed_becomes_due_after_the_interval():
    old = datetime.now(timezone.utc) - feeds.CHECK_INTERVAL - timedelta(seconds=1)
    assert feeds.due(old) is True


def test_naive_timestamps_are_treated_as_utc():
    """SQLite hands back datetimes without a timezone."""
    naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    assert feeds.due(naive) is True


def test_a_not_modified_response_reports_no_change(monkeypatch):
    """The whole point of sending the etag."""
    import sys, types

    module = types.ModuleType("feedparser")
    module.parse = lambda url, etag=None, modified=None: Parsed([], status=304)
    monkeypatch.setitem(sys.modules, "feedparser", module)
    monkeypatch.setattr(feeds, "_validate_feed_url", lambda url: None)

    _, etag, modified, changed = feeds.fetch("https://example.com/f.xml", "tag", "date")
    assert changed is False
    assert (etag, modified) == ("tag", "date")


def test_an_http_error_is_reported(monkeypatch):
    import sys, types

    module = types.ModuleType("feedparser")
    module.parse = lambda url, etag=None, modified=None: Parsed([], status=503)
    monkeypatch.setitem(sys.modules, "feedparser", module)
    monkeypatch.setattr(feeds, "_validate_feed_url", lambda url: None)

    with pytest.raises(feeds.FeedError):
        feeds.fetch("https://example.com/f.xml")


def test_something_that_is_not_a_feed_is_refused(monkeypatch):
    import sys, types

    module = types.ModuleType("feedparser")
    module.parse = lambda url, etag=None, modified=None: Parsed([], status=200, bozo=1)
    monkeypatch.setitem(sys.modules, "feedparser", module)
    monkeypatch.setattr(feeds, "_validate_feed_url", lambda url: None)

    with pytest.raises(feeds.FeedError):
        feeds.fetch("https://example.com/index.html")


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------


def test_a_filename_keeps_the_real_extension():
    episode = feeds.Episode("g", "Episode One", None, "https://x/show.m4a?token=1")
    assert feeds.filename_for(episode).endswith(".m4a")


def test_an_unknown_extension_becomes_mp3():
    episode = feeds.Episode("g", "Episode", None, "https://x/stream?id=9")
    assert feeds.filename_for(episode).endswith(".mp3")


def test_a_filename_strips_anything_awkward():
    episode = feeds.Episode("g", "Ep 3: What/Now?", None, "https://x/a.mp3")
    name = feeds.filename_for(episode)
    assert "/" not in name and "?" not in name and ":" not in name


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def signed_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


def stub_feed(monkeypatch, entries=None):
    """A feed that parses, without touching the network."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes, "parse_feed_url", lambda url: Parsed(entries if entries is not None else [entry()])
    )


def test_a_feed_can_be_watched(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    body = client.post("/api/feeds", json={"url": "https://example.com/f.xml"}).json()
    assert body["feed"]["title"] == "The Show"
    assert body["feed"]["active"] is True
    # Nothing is rendered without being asked.
    assert body["feed"]["auto_render"] is False
    assert body["feed"]["clip_count"] == 0


def test_a_bad_feed_fails_immediately(monkeypatch, tmp_path):
    """A typo should not become a feed that silently never produces anything."""
    import app.api.routes as routes

    client = signed_in(monkeypatch, tmp_path)

    def boom(url):
        raise routes.RssFetchError("Not a feed", status_code=400)

    monkeypatch.setattr(routes, "parse_feed_url", boom)
    assert client.post("/api/feeds", json={"url": "https://example.com/x"}).status_code == 400


def test_the_same_feed_is_not_watched_twice(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})
    assert client.post(
        "/api/feeds", json={"url": "https://example.com/f.xml"}
    ).status_code == 409


def test_feeds_are_listed_and_removable(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    feed = client.post("/api/feeds", json={"url": "https://example.com/f.xml"}).json()["feed"]
    assert len(client.get("/api/feeds").json()["feeds"]) == 1
    assert client.delete(f"/api/feeds/{feed['id']}").status_code == 200
    assert client.get("/api/feeds").json()["feeds"] == []


def test_a_feeds_settings_can_be_changed(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    feed = client.post("/api/feeds", json={"url": "https://example.com/f.xml"}).json()["feed"]
    body = client.patch(
        f"/api/feeds/{feed['id']}",
        json={"clip_count": 6, "auto_render": True, "aspect_ratio": "1:1"},
    ).json()
    assert body["feed"]["clip_count"] == 6
    assert body["feed"]["auto_render"] is True
    assert body["feed"]["aspect_ratio"] == "1:1"


def test_an_unknown_shape_is_refused(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    assert client.post(
        "/api/feeds", json={"url": "https://example.com/f.xml", "aspect_ratio": "3:7"}
    ).status_code == 400


def test_a_check_can_be_asked_for(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    body = client.post("/api/feeds/check").json()
    assert body["job"]["kind"] == "check_feeds"


def test_someone_elses_feed_is_not_found(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    feed = client.post("/api/feeds", json={"url": "https://example.com/f.xml"}).json()["feed"]

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get("/api/feeds").json()["feeds"] == []
    assert client.delete(f"/api/feeds/{feed['id']}").status_code == 404
    assert client.get(f"/api/feeds/{feed['id']}/episodes").status_code == 404


# --------------------------------------------------------------------------
# Checking, without the network
# --------------------------------------------------------------------------


def test_checking_queues_new_episodes_once(monkeypatch, tmp_path):
    """Run twice, import once — the property that matters unattended."""
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})

    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import Feed, FeedEpisode, Job, JobKind
    from app.db.session import SessionLocal

    monkeypatch.setattr(
        service, "fetch",
        lambda url, etag=None, modified=None: (Parsed([entry()]), "tag", "date", True),
    )

    def run_check():
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.kind == JobKind.check_feeds).first()
            if job is None:
                from app.db.models import User

                owner = db.query(User).first()
                job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
                db.add(job)
                db.commit()
            jobs._check_feeds(db, job)
            db.commit()

    run_check()
    with SessionLocal() as db:
        assert db.query(FeedEpisode).count() == 1
        assert db.query(Job).filter(Job.kind == JobKind.import_episode).count() == 1

    # Second sweep: the feed still lists the same episode.
    with SessionLocal() as db:
        db.query(Feed).first().last_checked = None
        db.commit()
    run_check()
    with SessionLocal() as db:
        assert db.query(FeedEpisode).count() == 1, "the episode was imported twice"


def test_a_first_sight_takes_only_the_newest(monkeypatch, tmp_path):
    """Subscribing to a show with a back catalogue must not queue all of it."""
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})

    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import FeedEpisode, Job, JobKind, User
    from app.db.session import SessionLocal

    many = [entry(guid=f"g{index}", title=f"Episode {index}") for index in range(40)]
    monkeypatch.setattr(
        service, "fetch",
        lambda url, etag=None, modified=None: (Parsed(many), None, None, True),
    )

    with SessionLocal() as db:
        owner = db.query(User).first()
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        jobs._check_feeds(db, job)
        db.commit()

    with SessionLocal() as db:
        assert db.query(FeedEpisode).count() == service.FIRST_RUN_LIMIT


def test_a_failing_feed_is_recorded_rather_than_crashing(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    stub_feed(monkeypatch)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})

    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import Feed, Job, JobKind, User
    from app.db.session import SessionLocal

    def boom(url, etag=None, modified=None):
        raise service.FeedError("host is down")

    monkeypatch.setattr(service, "fetch", boom)

    with SessionLocal() as db:
        owner = db.query(User).first()
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        jobs._check_feeds(db, job)
        db.commit()

    with SessionLocal() as db:
        feed = db.query(Feed).first()
        assert "host is down" in (feed.last_error or "")
        # And it was still marked as checked, so it is not retried in a tight loop.
        assert feed.last_checked is not None
