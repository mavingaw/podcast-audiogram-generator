"""What each platform will accept.

An export thirty seconds too long, or six megabytes over a cap, fails at the
upload step — after the render, after the wait, usually on a phone. These are
the checks that move that discovery to before the GPU time is spent.
"""

from __future__ import annotations

import pytest

from app.services.platforms import (
    GB,
    MB,
    PLATFORMS,
    check,
    check_all,
    destinations_for,
    get,
)
from app.services.scene import PLATFORM_SAFE_AREAS
from app.services.variants import RATIO_DIMENSIONS
from tests.test_api import create_test_client, register_second_user


# --------------------------------------------------------------------------
# The table itself
# --------------------------------------------------------------------------


def test_every_platform_is_internally_consistent():
    for platform in PLATFORMS:
        assert platform.min_seconds < platform.max_seconds, platform.key
        assert platform.max_bytes > 0, platform.key
        assert platform.ratios, platform.key
        assert platform.containers and platform.video_codecs and platform.audio_codecs
        assert platform.frame_rates, platform.key


def test_the_preferred_ratio_is_one_the_platform_accepts():
    for platform in PLATFORMS:
        assert platform.preferred_ratio in platform.ratios, platform.key


def test_every_ratio_named_is_one_this_app_can_render():
    """A destination asking for a shape we cannot produce is a dead end."""
    for platform in PLATFORMS:
        for ratio in platform.ratios:
            assert ratio in RATIO_DIMENSIONS, f"{platform.key}: {ratio}"


def test_safe_areas_point_at_guides_that_exist():
    for platform in PLATFORMS:
        if platform.safe_area is not None:
            assert platform.safe_area in PLATFORM_SAFE_AREAS, platform.key


def test_platform_keys_are_unique():
    keys = [platform.key for platform in PLATFORMS]
    assert len(keys) == len(set(keys))


def test_every_platform_records_when_it_was_checked():
    """These limits change without notice, so the table has to age visibly."""
    for platform in PLATFORMS:
        assert platform.checked, platform.key


def test_lookup_by_key_and_alias():
    assert get("tiktok").label == "TikTok"
    assert get("TikTok").key == "tiktok"
    # X was Twitter, and people still say Twitter.
    assert get("twitter").key == "x"
    assert get("nowhere") is None


# --------------------------------------------------------------------------
# Checking a clip
# --------------------------------------------------------------------------


def test_a_typical_vertical_clip_passes_the_vertical_platforms():
    for key in ("tiktok", "reels", "shorts", "facebook_reels"):
        verdict = check(get(key), "9:16", 36.0, 8 * MB)
        assert verdict.ok, (key, verdict.blocking)


def test_too_long_is_blocking():
    verdict = check(get("x"), "16:9", 240.0)
    assert not verdict.ok
    assert any("Too long" in reason for reason in verdict.blocking)


def test_too_short_is_blocking():
    verdict = check(get("pinterest"), "9:16", 2.0)
    assert not verdict.ok
    assert any("Too short" in reason for reason in verdict.blocking)


def test_too_large_is_blocking():
    verdict = check(get("tiktok"), "9:16", 30.0, 700 * MB)
    assert not verdict.ok
    assert any("Too large" in reason for reason in verdict.blocking)


def test_an_unsupported_shape_is_blocking():
    verdict = check(get("reels"), "16:9", 30.0)
    assert not verdict.ok
    assert any("not supported" in reason for reason in verdict.blocking)


def test_a_supported_but_unpreferred_shape_only_warns():
    """It will upload; it will just be a worse post."""
    verdict = check(get("tiktok"), "16:9", 30.0)
    assert verdict.ok
    assert verdict.warnings


def test_the_preferred_shape_warns_about_nothing():
    assert check(get("tiktok"), "9:16", 30.0).warnings == []


def test_a_wrong_container_is_blocking():
    verdict = check(get("reels"), "9:16", 30.0, container="avi")
    assert not verdict.ok
    assert any("not accepted" in reason for reason in verdict.blocking)


def test_a_wrong_codec_is_blocking():
    assert not check(get("linkedin"), "1:1", 30.0, video_codec="vp9").ok
    assert not check(get("tiktok"), "9:16", 30.0, audio_codec="opus").ok


def test_size_is_skipped_when_it_is_not_known_yet():
    """Before a render there is no file, and that must not read as a failure."""
    verdict = check(get("tiktok"), "9:16", 30.0, file_bytes=None)
    assert verdict.ok


def test_several_problems_are_all_reported():
    """Fixing one at a time is a bad way to find out about the other two."""
    verdict = check(get("snapchat"), "16:9", 600.0, 4 * GB)
    assert len(verdict.blocking) >= 3


# --------------------------------------------------------------------------
# The whole list
# --------------------------------------------------------------------------


def test_usable_destinations_come_first():
    results = check_all("9:16", 36.0, 8 * MB)
    ok_flags = [item["ok"] for item in results]
    assert ok_flags == sorted(ok_flags, reverse=True)


def test_clean_destinations_come_before_warned_ones():
    results = [item for item in check_all("9:16", 36.0, 8 * MB) if item["ok"]]
    warned = [bool(item["warnings"]) for item in results]
    assert warned == sorted(warned)


def test_every_destination_is_reported():
    assert len(check_all("9:16", 30.0)) == len(PLATFORMS)


def test_a_long_landscape_clip_is_mostly_refused():
    """Four minutes of 16:9 is a YouTube video, not a Reel."""
    results = {item["platform"]: item for item in check_all("16:9", 240.0, 700 * MB)}
    assert results["youtube"]["ok"]
    assert not results["shorts"]["ok"]
    assert not results["reels"]["ok"]
    assert not results["tiktok"]["ok"]


def test_destinations_for_a_shape_prefer_the_ones_built_for_it():
    first = destinations_for("9:16")[0]
    assert first.preferred_ratio == "9:16"


def test_destinations_for_an_unknown_shape_is_empty():
    assert destinations_for("3:7") == []


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def signed_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


def test_the_specs_are_served(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    body = client.get("/api/platforms").json()
    assert len(body["platforms"]) == len(PLATFORMS)
    for entry in body["platforms"]:
        assert entry["label"] and entry["ratios"] and entry["checked"]


def test_a_projects_destinations_are_reported(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Clip"}).json()["project"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"aspect_ratio": "9:16", "clip_start": 0.0, "clip_end": 36.0},
    )
    body = client.get(f"/api/projects/{project['id']}/destinations").json()

    assert body["aspect_ratio"] == "9:16"
    assert body["duration"] == 36.0
    # Nothing rendered yet, so size is unknown rather than failing.
    assert body["rendered"] is False
    assert body["file_bytes"] is None
    assert any(item["ok"] for item in body["destinations"])


def test_an_over_long_clip_reports_what_will_refuse_it(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Long"}).json()["project"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"aspect_ratio": "9:16", "clip_start": 0.0, "clip_end": 600.0},
    )
    body = client.get(f"/api/projects/{project['id']}/destinations").json()
    refused = {i["platform"] for i in body["destinations"] if not i["ok"]}
    assert "reels" in refused and "shorts" in refused


def test_a_rendered_clip_is_checked_against_its_real_size(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    from app.api.routes import settings

    project = client.post("/api/projects", json={"title": "Rendered"}).json()["project"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"aspect_ratio": "9:16", "clip_start": 0.0, "clip_end": 30.0},
    )
    outputs = settings.outputs_dir / project["id"]
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "audiogram.mp4").write_bytes(b"x" * (600 * MB))

    body = client.get(f"/api/projects/{project['id']}/destinations").json()
    assert body["rendered"] is True
    assert body["file_bytes"] == 600 * MB
    tiktok = next(i for i in body["destinations"] if i["platform"] == "tiktok")
    assert not tiktok["ok"]
    assert any("Too large" in reason for reason in tiktok["blocking"])


def test_someone_elses_project_is_not_found(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Mine"}).json()["project"]
    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get(f"/api/projects/{project['id']}/destinations").status_code == 404


def test_every_platform_says_where_to_post():
    from app.services.platforms import PLATFORMS, check_all

    for spec in PLATFORMS:
        assert spec.upload_url.startswith("https://"), spec.key
    rows = check_all("9:16", 30.0, 5 * 1024 * 1024)
    assert rows and all(r["upload_url"] for r in rows)
    # Instagram only takes uploads from its phone app; the row says so.
    by_key = {r["platform"]: r for r in rows}
    assert by_key["reels"]["web_upload"] is False
    assert by_key["tiktok"]["web_upload"] is True
