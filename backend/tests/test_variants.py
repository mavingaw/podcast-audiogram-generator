from __future__ import annotations

import pytest

from app.services.variants import (
    RATIO_DIMENSIONS,
    Remap,
    VariantError,
    remap_layer,
    remap_scene,
    variant_title,
)
from tests.test_api import create_test_client, register_second_user


def signed_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def test_a_layer_keeps_its_pixel_size_across_ratios():
    """9% of a 1920px frame and 9% of a 1080px frame are different designs."""
    remap = Remap(RATIO_DIMENSIONS["9:16"], RATIO_DIMENSIONS["1:1"])
    layer = {"id": "w", "x": 10, "y": 40, "width": 80, "height": 9}
    moved = remap_layer(layer, remap)

    # 9% of 1920 is 172.8px; as a share of 1080 that is 16%.
    assert moved["height"] == pytest.approx(16.0, abs=0.1)
    # Width was already relative to 1080 in both, so it is unchanged.
    assert moved["width"] == pytest.approx(80.0, abs=0.1)


def test_a_layer_keeps_its_centre():
    remap = Remap(RATIO_DIMENSIONS["9:16"], RATIO_DIMENSIONS["16:9"])
    layer = {"id": "t", "x": 10, "y": 40, "width": 80, "height": 6}
    moved = remap_layer(layer, remap)

    before = layer["x"] + layer["width"] / 2
    after = moved["x"] + moved["width"] / 2
    assert after == pytest.approx(before, abs=0.5)


def test_a_layer_never_leaves_the_frame():
    """Scaling up can push a layer off the edge; it must be pulled back."""
    remap = Remap(RATIO_DIMENSIONS["9:16"], RATIO_DIMENSIONS["16:9"])
    layer = {"id": "t", "x": 5, "y": 88, "width": 90, "height": 10}
    moved = remap_layer(layer, remap)

    assert moved["x"] >= 0
    assert moved["y"] >= 0
    assert moved["x"] + moved["width"] <= 100.001
    assert moved["y"] + moved["height"] <= 100.001


def test_layer_properties_other_than_geometry_survive():
    remap = Remap(RATIO_DIMENSIONS["9:16"], RATIO_DIMENSIONS["1:1"])
    layer = {
        "id": "art", "type": "artwork", "mediaId": "cover", "radius": 0.1,
        "color": "#ffe066", "text": "Episode 12", "visible": True,
        "x": 10, "y": 10, "width": 40, "height": 22,
    }
    moved = remap_layer(layer, remap)
    for key in ("id", "type", "mediaId", "radius", "color", "text", "visible"):
        assert moved[key] == layer[key]


def test_the_platform_guide_follows_the_shape():
    scene = {"platform": "tiktok", "layers": []}
    assert remap_scene(scene, "9:16", "1:1")["platform"] == "feed"
    # Landscape has no platform chrome worth guarding against.
    assert "platform" not in remap_scene(scene, "9:16", "16:9")


def test_remapping_does_not_mutate_the_original():
    scene = {"layers": [{"id": "a", "x": 10, "y": 10, "width": 50, "height": 10}]}
    remap_scene(scene, "9:16", "1:1")
    assert scene["layers"][0]["height"] == 10


def test_an_unknown_ratio_is_refused():
    with pytest.raises(VariantError):
        remap_scene({}, "9:16", "3:7")


def test_variant_titles_do_not_stack():
    assert variant_title("Episode 12", "1:1") == "Episode 12 (Square)"
    # Varying a variant replaces the label rather than appending another.
    assert variant_title("Episode 12 (Square)", "16:9") == "Episode 12 (Landscape)"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_ratios_are_advertised_with_what_they_are_for(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    ratios = client.get("/api/ratios").json()["ratios"]
    assert {r["ratio"] for r in ratios} == set(RATIO_DIMENSIONS)
    assert all(r["for"] and r["label"] and r["dimensions"] for r in ratios)


def test_creating_variants_copies_the_project_and_queues_renders(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    client.patch(
        f"/api/projects/{project['id']}",
        json={"aspect_ratio": "9:16",
              "scene": {"layers": [{"id": "art", "type": "artwork",
                                    "x": 10, "y": 40, "width": 80, "height": 9}]}},
    )

    result = client.post(
        f"/api/projects/{project['id']}/variants", json={"ratios": ["1:1", "16:9"]}
    ).json()

    assert [p["aspect_ratio"] for p in result["projects"]] == ["1:1", "16:9"]
    assert len(result["jobs"]) == 2
    assert all(job["kind"] == "render" for job in result["jobs"])
    # The geometry was remapped, not copied verbatim. Checked on artwork rather
    # than the waveform: the waveform is settled clear of the caption band after
    # remapping, which can land it back at the height it started with.
    square = result["projects"][0]["scene"]["layers"][0]
    assert square["height"] > 9


def test_a_variant_of_the_current_ratio_is_skipped(monkeypatch, tmp_path):
    """Varying into the shape it already is would just be a duplicate."""
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    client.patch(f"/api/projects/{project['id']}", json={"aspect_ratio": "9:16"})

    result = client.post(
        f"/api/projects/{project['id']}/variants", json={"ratios": ["9:16", "1:1"]}
    ).json()
    assert [p["aspect_ratio"] for p in result["projects"]] == ["1:1"]


def test_variants_can_be_created_without_rendering(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    result = client.post(
        f"/api/projects/{project['id']}/variants",
        json={"ratios": ["1:1"], "render": False},
    ).json()
    assert result["jobs"] == []
    assert len(result["projects"]) == 1


def test_duplicate_ratios_produce_one_variant_each(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    result = client.post(
        f"/api/projects/{project['id']}/variants",
        json={"ratios": ["1:1", "1:1", "16:9"], "render": False},
    ).json()
    assert [p["aspect_ratio"] for p in result["projects"]] == ["1:1", "16:9"]


def test_an_unknown_ratio_is_rejected_by_the_api(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    assert client.post(
        f"/api/projects/{project['id']}/variants", json={"ratios": ["3:7"]}
    ).status_code == 400


def test_variants_of_someone_elses_project_are_not_found(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.post(
        f"/api/projects/{project['id']}/variants", json={"ratios": ["1:1"]}
    ).status_code == 404


# --------------------------------------------------------------------------
# Captions across shapes
# --------------------------------------------------------------------------


def test_captions_stay_the_same_relative_size_in_every_shape(tmp_path):
    """Height-based sizing gave landscape a third the caption of vertical."""
    from app.services.jobs import _dimensions, _write_ass
    from app.services.scene import parse as parse_scene

    scene = parse_scene({"captionPreset": "social"}, 10.0)
    shares = {}
    for ratio in ("9:16", "4:5", "1:1", "16:9"):
        path = tmp_path / f"{ratio.replace(':', 'x')}.ass"
        _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], ratio, scene)
        style = next(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Style:")
        ).split(",")
        width, _ = _dimensions(ratio)
        shares[ratio] = int(style[2]) / width

    # Every shape reads at the same size relative to the frame's width.
    assert max(shares.values()) - min(shares.values()) < 0.005, shares


def test_captions_still_clear_the_platform_band_in_every_shape(tmp_path):
    """The margin is vertical, so it must stay a share of height."""
    from app.services.jobs import _dimensions, _write_ass
    from app.services.scene import parse as parse_scene

    scene = parse_scene({"captionPreset": "social"}, 10.0)
    for ratio in ("9:16", "4:5", "1:1", "16:9"):
        path = tmp_path / f"{ratio.replace(':', 'x')}.ass"
        _write_ass(path, [{"start": 0, "end": 2, "text": "hook"}], ratio, scene)
        style = next(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Style:")
        ).split(",")
        _, height = _dimensions(ratio)
        margin = int(style[-2])
        assert margin / height > 0.25, f"{ratio} margin {margin} of {height}"


def test_the_caption_budget_matches_what_actually_fits():
    """The splitter and the burned-in font have to agree.

    They did not: lines were cut at a flat 42 characters while only about 18
    fit, so libass re-wrapped each one into three and the caption block grew
    tall enough to collide with the waveform above it.
    """
    from app.services.scene import CAPTION_PRESETS, caption_char_budget

    for name, preset in CAPTION_PRESETS.items():
        budget = caption_char_budget(name)
        # A line at the budget must fit inside the usable width at that size.
        line_width = budget * preset["size_ratio"] * 0.5
        assert line_width <= 0.84 + 0.02, f"{name}: {budget} chars overflows"
        # And it must not be so cautious that it wastes the frame.
        assert line_width > 0.6, f"{name}: {budget} chars wastes the line"


def test_a_bigger_preset_gets_a_smaller_budget():
    from app.services.scene import caption_char_budget

    assert caption_char_budget("shout") < caption_char_budget("social")
    assert caption_char_budget("social") < caption_char_budget("clean")


def test_clip_captions_respect_the_preset_budget():
    from app.services.jobs import _clip_captions
    from app.services.scene import caption_char_budget

    words = "one two three four five six seven eight nine ten eleven twelve".split()
    segment = {
        "id": 1, "speaker": "s", "start": 0.0, "end": 6.0,
        "text": " ".join(words),
        "words": [
            {"text": word, "start": index * 0.5, "end": index * 0.5 + 0.5}
            for index, word in enumerate(words)
        ],
    }
    transcript = {"language": "en", "duration": 10.0, "segments": [segment]}

    budget = caption_char_budget("shout")
    for line in _clip_captions(transcript, 0.0, 10.0, max_chars=budget):
        # A little slack: a single long word cannot be split.
        assert len(line["text"]) <= budget + 8, line


# --------------------------------------------------------------------------
# The waveform must stay clear of the captions after a shape change
# --------------------------------------------------------------------------


def default_scene(preset: str = "social") -> dict:
    """The layout a new 9:16 project starts with."""
    return {
        "captionPreset": preset,
        "layers": [{"id": "waveform", "type": "waveform",
                    "x": 12, "y": 71, "width": 76, "height": 9}],
    }


def caption_end(preset: str) -> float:
    from app.services.scene import CAPTION_PRESETS

    return 1.0 - CAPTION_PRESETS[preset]["margin_ratio"]


@pytest.mark.parametrize("target", ["4:5", "1:1", "16:9"])
def test_a_variant_does_not_draw_the_waveform_through_its_captions(target):
    """Remapping preserves pixel size and centre; the caption band does not.

    Captions are placed by a margin that is a share of height, so changing
    shape slides the caption band underneath a waveform that stayed where it
    was. Every variant of a default project used to collide.
    """
    wave = remap_scene(default_scene(), "9:16", target)["layers"][0]
    top = wave["y"] / 100
    assert top >= caption_end("social") - 0.001, (
        f"{target}: waveform starts at {top:.2f}, captions run to "
        f"{caption_end('social'):.2f}"
    )


@pytest.mark.parametrize("target", ["4:5", "1:1"])
def test_a_vertical_variant_keeps_the_waveform_out_of_the_platform_band(target):
    wave = remap_scene(default_scene(), "9:16", target)["layers"][0]
    assert (wave["y"] + wave["height"]) / 100 <= 0.801, target


def test_a_landscape_variant_may_use_the_lower_frame():
    """No platform chrome in landscape, so the floor is the frame edge."""
    wave = remap_scene(default_scene(), "9:16", "16:9")["layers"][0]
    bottom = (wave["y"] + wave["height"]) / 100
    assert 0.80 < bottom <= 0.941


@pytest.mark.parametrize("preset", ["social", "boxed", "shout", "kinder", "clean"])
def test_the_waveform_clears_whichever_caption_preset_is_in_use(preset):
    """The band moves with the preset, so the settling has to read it."""
    for target in ("4:5", "1:1", "16:9"):
        wave = remap_scene(default_scene(preset), "9:16", target)["layers"][0]
        assert wave["y"] / 100 >= caption_end(preset) - 0.001, (preset, target)


def test_a_waveform_that_is_already_clear_is_left_alone():
    scene = {
        "captionPreset": "social",
        "layers": [{"id": "waveform", "type": "waveform",
                    "x": 10, "y": 74, "width": 80, "height": 5}],
    }
    wave = remap_scene(scene, "1:1", "1:1")["layers"][0]
    assert wave["y"] == 74
    assert wave["height"] == 5


def test_settling_never_shrinks_the_waveform_away():
    from app.services.variants import MIN_WAVE_HEIGHT, settle_waveform

    scene = {
        "captionPreset": "shout",
        "layers": [{"id": "waveform", "type": "waveform",
                    "x": 10, "y": 95, "width": 80, "height": 40}],
    }
    wave = settle_waveform(scene, "9:16")["layers"][0]
    assert wave["height"] >= MIN_WAVE_HEIGHT


def test_settling_leaves_other_layers_untouched():
    scene = {
        "captionPreset": "social",
        "layers": [
            {"id": "art", "type": "artwork", "x": 12, "y": 13, "width": 76, "height": 41},
            {"id": "waveform", "type": "waveform", "x": 12, "y": 60, "width": 76, "height": 9},
        ],
    }
    art = remap_scene(scene, "9:16", "16:9")["layers"][0]
    # Artwork keeps whatever the geometric remap produced.
    assert art["type"] == "artwork"
    assert art["y"] != 71
