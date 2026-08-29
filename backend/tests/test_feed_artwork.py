"""The show's logo becomes the background of every clip cut from its feed.

A podcast's artwork is the one image guaranteed to exist for it, and a clip
on a flat colour when the logo is right there in the feed was the first thing
a person noticed about the output.
"""

from __future__ import annotations

import json

import pytest

from app.services import feeds
from app.services.batching import with_artwork


def parsed_with(image=None, itunes=None, entry_image=None):
    """A feedparser-shaped document, without feedparser."""
    import feedparser

    channel = ""
    if image:
        channel += f"<image><url>{image}</url></image>"
    if itunes:
        channel += f'<itunes:image href="{itunes}"/>'
    entry = ""
    if entry_image:
        entry = f'<itunes:image href="{entry_image}"/>'
    xml = f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>A show</title>{channel}
    <item><title>Ep</title><guid>g1</guid>{entry}
      <enclosure url="https://example.com/g1.mp3" type="audio/mpeg" length="10"/>
    </item>
  </channel>
</rss>"""
    return feedparser.parse(xml.encode())


# --------------------------------------------------------------------------
# Reading it out of the feed
# --------------------------------------------------------------------------


def test_the_itunes_image_is_the_show_artwork():
    assert feeds.artwork_of(parsed_with(itunes="https://cdn/x/logo.jpg")) == "https://cdn/x/logo.jpg"


def test_the_older_rss_image_is_a_fallback():
    assert feeds.artwork_of(parsed_with(image="https://cdn/x/old.png")) == "https://cdn/x/old.png"


def test_a_feed_with_no_artwork_says_so():
    assert feeds.artwork_of(parsed_with()) is None
    assert feeds.artwork_of(None) is None


def test_an_episode_can_carry_its_own_artwork():
    parsed = parsed_with(itunes="https://cdn/show.jpg", entry_image="https://cdn/ep.jpg")
    episodes = feeds.episodes_of(parsed)
    assert episodes[0].image_url == "https://cdn/ep.jpg"


def test_an_episode_without_its_own_has_none():
    assert feeds.episodes_of(parsed_with(itunes="https://cdn/show.jpg"))[0].image_url is None


def test_a_non_http_image_is_ignored():
    """`javascript:` or a bare filename is not something to fetch."""
    assert feeds._image_href({"href": "javascript:alert(1)"}) is None
    assert feeds._image_href({"href": "logo.png"}) is None
    assert feeds._image_href("not a dict or url") is None


# --------------------------------------------------------------------------
# Fetching it
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def stub(monkeypatch, body=bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]), content_type="image/png"):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Response(body, content_type))
    monkeypatch.setattr(feeds, "_validate_feed_url", lambda url: None)


def test_the_extension_comes_from_the_content_type_not_the_url(monkeypatch, tmp_path):
    """Feeds serve JPEGs from paths ending in .png all the time."""
    stub(monkeypatch, body=bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"rest", content_type="image/jpeg")
    name, kind, size = feeds.download_image("https://cdn/logo.png", tmp_path)
    assert name.endswith(".jpg")
    assert kind == "image/jpeg"
    assert (tmp_path / name).exists()


def test_a_jpeg_served_as_image_jpg_is_accepted(monkeypatch, tmp_path):
    """Not a registered type; what Spreaker's CDN sends for every logo. The
    live feed's artwork was refused for exactly this."""
    stub(monkeypatch, body=bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"rest", content_type="image/jpg")
    name, kind, _ = feeds.download_image("https://cdn/logo", tmp_path)
    assert name.endswith(".jpg") and kind == "image/jpeg"


def test_the_bytes_outrank_a_useless_header(monkeypatch, tmp_path):
    png = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]) + b"rest"
    stub(monkeypatch, body=png, content_type="application/octet-stream")
    name, kind, _ = feeds.download_image("https://cdn/logo", tmp_path)
    assert name.endswith(".png") and kind == "image/png"


def test_something_that_is_not_an_image_is_refused(monkeypatch, tmp_path):
    stub(monkeypatch, body=b"<html>", content_type="text/html")
    with pytest.raises(feeds.FeedError, match="not an image"):
        feeds.download_image("https://cdn/logo", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_empty_image_is_refused(monkeypatch, tmp_path):
    stub(monkeypatch, body=b"")
    with pytest.raises(feeds.FeedError):
        feeds.download_image("https://cdn/logo.png", tmp_path)


def test_an_absurdly_large_image_is_refused(monkeypatch, tmp_path):
    stub(monkeypatch, body=b"x" * (feeds.MAX_ARTWORK_BYTES + 1))
    with pytest.raises(feeds.FeedError, match="large"):
        feeds.download_image("https://cdn/logo.png", tmp_path)


# --------------------------------------------------------------------------
# Landing in the clip
# --------------------------------------------------------------------------


def test_artwork_goes_into_the_background_and_the_artwork_slot():
    scene = with_artwork(
        {"layers": [{"id": "a", "type": "artwork"}, {"id": "w", "type": "waveform"}]},
        "img-1",
    )
    assert scene["backgroundImage"]["mediaId"] == "img-1"
    assert scene["layers"][0]["mediaId"] == "img-1"
    assert "mediaId" not in scene["layers"][1]


def test_the_background_is_dimmed_so_captions_stay_legible():
    scene = with_artwork({}, "img-1")
    assert scene["backgroundImage"]["dim"] > 0
    assert scene["backgroundImage"]["blur"] > 0


def test_a_template_that_chose_its_own_background_keeps_it():
    """The logo fills a gap; it does not overrule a decision."""
    scene = with_artwork(
        {"backgroundImage": {"mediaId": "chosen", "blur": 0, "dim": 0},
         "layers": [{"id": "a", "type": "artwork", "mediaId": "chosen-art"}]},
        "img-1",
    )
    assert scene["backgroundImage"]["mediaId"] == "chosen"
    assert scene["layers"][0]["mediaId"] == "chosen-art"


def test_a_scene_without_layers_still_gets_a_background():
    scene = with_artwork({}, "img-1")
    assert scene["backgroundImage"]["mediaId"] == "img-1"
    assert "layers" not in scene


def test_the_original_scene_is_not_mutated():
    original = {"layers": [{"id": "a", "type": "artwork"}]}
    with_artwork(original, "img-1")
    assert "mediaId" not in original["layers"][0]
    assert "backgroundImage" not in original


# --------------------------------------------------------------------------
# Through the feed check
# --------------------------------------------------------------------------


def test_checking_a_feed_stores_its_artwork_as_an_image_asset(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})

    import app.api.routes as routes
    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import Feed, Job, JobKind, MediaAsset, User
    from app.db.session import SessionLocal

    parsed = parsed_with(itunes="https://cdn/show.jpg")
    monkeypatch.setattr(routes, "parse_feed_url", lambda url: parsed)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})

    monkeypatch.setattr(
        service, "fetch",
        lambda url, etag=None, modified=None: (parsed, None, None, True, None),
    )
    monkeypatch.setattr(
        service, "download_image",
        lambda url, target_dir: ("stored-logo.jpg", "image/jpeg", 1234),
    )

    with SessionLocal() as db:
        owner = db.query(User).first()
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        jobs._check_feeds(db, job)
        db.commit()

        feed = db.query(Feed).first()
        assert feed.artwork_url == "https://cdn/show.jpg"
        artwork = db.get(MediaAsset, feed.artwork_media_id)
        assert artwork is not None
        assert artwork.content_type == "image/jpeg"
        assert artwork.stored_name == "stored-logo.jpg"


def test_a_failed_artwork_fetch_does_not_fail_the_check(monkeypatch, tmp_path):
    """The logo is a bonus on a feed that is otherwise working."""
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})

    import app.api.routes as routes
    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import Feed, FeedEpisode, Job, JobKind, User
    from app.db.session import SessionLocal

    parsed = parsed_with(itunes="https://cdn/show.jpg")
    monkeypatch.setattr(routes, "parse_feed_url", lambda url: parsed)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})
    monkeypatch.setattr(
        service, "fetch",
        lambda url, etag=None, modified=None: (parsed, None, None, True, None),
    )

    def boom(url, target_dir):
        raise service.FeedError("cdn is down")

    monkeypatch.setattr(service, "download_image", boom)

    with SessionLocal() as db:
        owner = db.query(User).first()
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        jobs._check_feeds(db, job)
        db.commit()
        assert db.query(Feed).first().artwork_media_id is None
        # The episode was still queued.
        assert db.query(FeedEpisode).count() == 1


def test_media_reports_its_artwork(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})

    from app.db.models import MediaAsset, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        owner = db.query(User).first()
        logo = MediaAsset(owner_id=owner.id, original_name="logo.png", stored_name="l.png",
                          content_type="image/png", size_bytes=1)
        db.add(logo)
        db.flush()
        episode = MediaAsset(owner_id=owner.id, original_name="ep.mp3", stored_name="e.mp3",
                             content_type="audio/mpeg", size_bytes=1, artwork_media_id=logo.id)
        db.add(episode)
        db.commit()
        logo_id, episode_id = logo.id, episode.id

    listed = {m["id"]: m for m in client.get("/api/media").json()["media"]}
    assert listed[episode_id]["artwork_media_id"] == logo_id
    assert listed[logo_id]["artwork_media_id"] is None


def test_a_feed_without_artwork_yet_is_read_in_full(monkeypatch, tmp_path):
    """A 304 carries no document; with the tokens sent, the logo of an
    unchanged feed would wait for its next episode. That happened."""
    from tests.test_api import create_test_client

    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "owner", "password": "Passw0rd!enough"})

    import app.api.routes as routes
    import app.services.feeds as service
    import app.services.jobs as jobs
    from app.db.models import Feed, Job, JobKind, User
    from app.db.session import SessionLocal

    parsed = parsed_with(itunes="https://cdn/show.jpg")
    monkeypatch.setattr(routes, "parse_feed_url", lambda url: parsed)
    client.post("/api/feeds", json={"url": "https://example.com/f.xml"})

    seen = {}

    def fake_fetch(url, etag=None, modified=None):
        seen["etag"], seen["modified"] = etag, modified
        return parsed, "tag", "date", True, None

    monkeypatch.setattr(service, "fetch", fake_fetch)
    monkeypatch.setattr(service, "download_image",
                        lambda url, target_dir: ("logo.jpg", "image/jpeg", 10))

    with SessionLocal() as db:
        feed = db.query(Feed).first()
        feed.etag, feed.last_modified = "old-tag", "old-date"
        owner = db.query(User).first()
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        jobs._check_feeds(db, job)
        db.commit()
        assert seen == {"etag": None, "modified": None}, "tokens were sent"
        assert db.query(Feed).first().artwork_media_id is not None

        # Now that the artwork is held, the polite conditional GET returns.
        job = Job(owner_id=owner.id, kind=JobKind.check_feeds)
        db.add(job)
        db.commit()
        db.query(Feed).first().last_checked = None
        db.commit()
        jobs._check_feeds(db, job)
        assert seen["etag"] == "tag"
