from __future__ import annotations

from app.services.templates import apply_template, scene_for_template
from tests.test_api import create_test_client, register_second_user


def signed_in(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "mujin", "password": "long-password"})
    return client


DESIGN = {
    "waveStyle": "envelope",
    "captionPreset": "shout",
    "backgroundImage": {"mediaId": "cover-a", "blur": 20, "dim": 0.4},
    "music": {"soundId": "chip-7", "gain": -18},
    "layers": [
        {"id": "art", "type": "artwork", "mediaId": "cover-a", "x": 10, "y": 8,
         "width": 50, "height": 28},
        {"id": "wave", "type": "waveform", "color": "#ffe066", "x": 10, "y": 60,
         "width": 80, "height": 9},
    ],
}


# --------------------------------------------------------------------------
# What a template keeps and what it drops
# --------------------------------------------------------------------------


def test_a_template_keeps_the_look():
    design = scene_for_template(DESIGN)
    assert design["waveStyle"] == "envelope"
    assert design["captionPreset"] == "shout"
    assert design["layers"][1]["color"] == "#ffe066"
    assert design["layers"][1]["height"] == 9


def test_a_template_drops_the_episode_media():
    """Otherwise last week's cover art lands on this week's clip."""
    design = scene_for_template(DESIGN)
    assert "mediaId" not in design["layers"][0]
    assert "mediaId" not in design["backgroundImage"]
    assert "music" not in design


def test_the_background_treatment_survives_without_the_image():
    design = scene_for_template(DESIGN)
    assert design["backgroundImage"] == {"blur": 20, "dim": 0.4}


def test_a_background_with_nothing_but_an_image_is_dropped_entirely():
    design = scene_for_template({"backgroundImage": {"mediaId": "cover-a"}})
    assert "backgroundImage" not in design


def test_saving_a_template_does_not_mutate_the_project_scene():
    scene_for_template(DESIGN)
    assert DESIGN["layers"][0]["mediaId"] == "cover-a"
    assert DESIGN["music"]["soundId"] == "chip-7"


def test_a_non_dict_scene_is_tolerated():
    assert scene_for_template(None) == {}
    assert scene_for_template([]) == {}


# --------------------------------------------------------------------------
# Applying one
# --------------------------------------------------------------------------


def test_applying_a_template_keeps_this_episodes_media():
    template = scene_for_template(DESIGN)
    current = {
        "layers": [{"id": "art", "type": "artwork", "mediaId": "cover-b",
                    "x": 0, "y": 0, "width": 20, "height": 20}],
        "backgroundImage": {"mediaId": "bg-b"},
        "music": {"soundId": "chip-2", "gain": -22},
    }
    applied = apply_template(current, template)

    assert applied["layers"][0]["mediaId"] == "cover-b"
    assert applied["backgroundImage"]["mediaId"] == "bg-b"
    assert applied["music"] == {"soundId": "chip-2", "gain": -22}
    # And it took on the design.
    assert applied["captionPreset"] == "shout"
    assert applied["layers"][0]["width"] == 50


def test_applying_a_template_to_a_project_with_no_media_leaves_it_bare():
    applied = apply_template({}, scene_for_template(DESIGN))
    assert "mediaId" not in applied["layers"][0]
    assert "music" not in applied


def test_applying_does_not_mutate_the_template():
    template = scene_for_template(DESIGN)
    apply_template({"layers": [{"id": "art", "mediaId": "cover-b"}]}, template)
    assert "mediaId" not in template["layers"][0]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_saving_and_listing_a_template(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    client.patch(f"/api/projects/{project['id']}", json={"scene": DESIGN})

    saved = client.post(
        "/api/templates", json={"name": "Show look", "project_id": project["id"]}
    ).json()["template"]
    assert saved["name"] == "Show look"
    assert saved["scene"]["captionPreset"] == "shout"
    assert "mediaId" not in saved["scene"]["layers"][0]

    listed = client.get("/api/templates").json()["templates"]
    assert [item["id"] for item in listed] == [saved["id"]]


def test_applying_a_template_through_the_api(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    source = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    client.patch(f"/api/projects/{source['id']}", json={"scene": DESIGN})
    template = client.post(
        "/api/templates", json={"name": "Show look", "project_id": source["id"]}
    ).json()["template"]

    target = client.post("/api/projects", json={"title": "Episode 13"}).json()["project"]
    client.patch(
        f"/api/projects/{target['id']}",
        json={"scene": {"layers": [{"id": "art", "mediaId": "cover-13"}]}},
    )

    applied = client.post(
        f"/api/projects/{target['id']}/template/{template['id']}"
    ).json()["project"]
    assert applied["scene"]["captionPreset"] == "shout"
    assert applied["scene"]["layers"][0]["mediaId"] == "cover-13"


def test_a_template_saved_in_one_shape_is_remapped_into_another(monkeypatch, tmp_path):
    """A vertical design dropped onto a landscape project must be remapped."""
    client = signed_in(monkeypatch, tmp_path)
    source = client.post("/api/projects", json={"title": "Vertical"}).json()["project"]
    client.patch(
        f"/api/projects/{source['id']}",
        json={"aspect_ratio": "9:16",
              "scene": {"layers": [{"id": "wave", "x": 10, "y": 40,
                                    "width": 80, "height": 9}]}},
    )
    template = client.post(
        "/api/templates", json={"name": "Vertical look", "project_id": source["id"]}
    ).json()["template"]
    assert template["aspect_ratio"] == "9:16"

    target = client.post("/api/projects", json={"title": "Wide"}).json()["project"]
    client.patch(f"/api/projects/{target['id']}", json={"aspect_ratio": "16:9"})
    applied = client.post(
        f"/api/projects/{target['id']}/template/{template['id']}"
    ).json()["project"]

    # 9% of a 1920-tall frame is not 9% of a 1080-tall one.
    assert applied["scene"]["layers"][0]["height"] != 9


def test_deleting_a_template(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    template = client.post(
        "/api/templates", json={"name": "Show look", "project_id": project["id"]}
    ).json()["template"]

    assert client.delete(f"/api/templates/{template['id']}").status_code == 200
    assert client.get("/api/templates").json()["templates"] == []
    assert client.delete(f"/api/templates/{template['id']}").status_code == 404


def test_templates_are_private_to_their_owner(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    template = client.post(
        "/api/templates", json={"name": "Show look", "project_id": project["id"]}
    ).json()["template"]

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.get("/api/templates").json()["templates"] == []
    assert client.delete(f"/api/templates/{template['id']}").status_code == 404

    other = client.post("/api/projects", json={"title": "Theirs"}).json()["project"]
    assert client.post(
        f"/api/projects/{other['id']}/template/{template['id']}"
    ).status_code == 404


def test_saving_a_template_from_someone_elses_project_is_refused(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]

    client.post("/api/auth/logout")
    register_second_user(client, "guest", "another-password")
    assert client.post(
        "/api/templates", json={"name": "Stolen", "project_id": project["id"]}
    ).status_code == 404


def test_a_template_needs_a_name(monkeypatch, tmp_path):
    client = signed_in(monkeypatch, tmp_path)
    project = client.post("/api/projects", json={"title": "Episode 12"}).json()["project"]
    assert client.post(
        "/api/templates", json={"name": "", "project_id": project["id"]}
    ).status_code == 422



# --------------------------------------------------------------------------
# Timing across clips of different lengths
# --------------------------------------------------------------------------


def test_a_template_never_carries_cuts_or_effect_cues():
    """Cuts are source-time ranges into one recording; cues sit on one clip."""
    design = scene_for_template({"cuts": [{"start": 1, "end": 2}], "sfx": [{"soundId": "x", "at": 1}]})
    assert "cuts" not in design and "sfx" not in design
    applied = apply_template({}, {"cuts": [{"start": 1, "end": 2}], "sfx": [{"soundId": "x", "at": 1}]}, 30)
    assert "cuts" not in applied and "sfx" not in applied


def test_a_layer_that_ran_to_the_end_runs_to_the_new_end():
    """Designed on a 45s clip, applied to a 90s one: the title must not vanish at 45s."""
    design = scene_for_template(
        {"layers": [{"id": "t", "type": "title", "startTime": 0, "endTime": 45}]}, clip_seconds=45
    )
    assert "endTime" not in design["layers"][0]
    applied = apply_template({}, design, clip_seconds=90)
    assert "endTime" not in applied["layers"][0]


def test_a_deliberate_short_window_is_kept():
    """A title shown for the first five seconds stays five seconds."""
    design = scene_for_template(
        {"layers": [{"id": "t", "type": "title", "startTime": 0, "endTime": 5}]}, clip_seconds=45
    )
    assert design["layers"][0]["endTime"] == 5
    assert apply_template({}, design, clip_seconds=90)["layers"][0]["endTime"] == 5


def test_a_window_past_a_shorter_clip_is_fitted_to_it():
    design = {"layers": [
        {"id": "late", "type": "title", "startTime": 40, "endTime": 44},
        {"id": "long", "type": "title", "startTime": 0, "endTime": 44},
    ]}
    applied = apply_template({}, design, clip_seconds=20)
    assert applied["layers"][0]["startTime"] == 0, "a layer that started past the end never appeared"
    assert "endTime" not in applied["layers"][1]
