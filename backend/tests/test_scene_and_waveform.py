from __future__ import annotations

import base64
import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app.services.scene import (
    DEFAULT_ACCENT,
    DEFAULT_BACKGROUND,
    WAVE_STYLES,
    enable_expression,
    escape_drawtext,
    ffmpeg_color,
)
from app.services.scene import parse as parse_scene
from app.services.waveform import decode, resample
from tests.test_api import create_test_client


def write_tone(path: Path, seconds: float = 2.0, amplitude: float = 0.5) -> Path:
    """A wav whose second half is silent, so peaks have something to show."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    frames = []
    for index in range(int(rate * seconds)):
        loud = index < rate * seconds / 2
        value = int(amplitude * 32767 * math.sin(index * 0.05)) if loud else 0
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(frames))
    return path


def envelope(values: list[int], rate: int = 10) -> str:
    return json.dumps(
        {
            "version": 1,
            "rate": rate,
            "count": len(values),
            "duration": len(values) / rate,
            "peaks": base64.b64encode(bytes(values)).decode("ascii"),
        }
    )


# --------------------------------------------------------------------------
# Peak envelope
# --------------------------------------------------------------------------


def test_decode_survives_missing_and_malformed_envelopes():
    assert decode(None) is None
    assert decode("") is None
    assert decode("not json") is None
    assert decode(json.dumps({"nope": 1})) is None


def test_resample_reduces_to_the_requested_bucket_count():
    stored = envelope([index % 256 for index in range(1000)])
    assert len(resample(stored, 50)) == 50
    assert len(resample(stored, 1)) == 1


def test_resample_returns_the_source_when_asked_for_more_than_it_has():
    stored = envelope([0, 128, 255])
    assert resample(stored, 100) == [0.0, 128 / 255, 1.0]


def test_resample_keeps_peaks_rather_than_averaging_them():
    # One loud transient in an otherwise silent second must survive the
    # downsample, because that spike is how you find a clip boundary.
    stored = envelope([0] * 49 + [255] + [0] * 50)
    assert max(resample(stored, 4)) == 1.0


def test_resample_windows_by_time():
    # Ten seconds at 10 Hz: silent first half, loud second half.
    stored = envelope([0] * 50 + [200] * 50)
    assert max(resample(stored, 10, start=0.0, end=5.0)) == 0.0
    assert max(resample(stored, 10, start=5.0, end=10.0)) == pytest.approx(200 / 255)


def test_resample_rejects_an_inverted_window():
    stored = envelope([100] * 100)
    assert resample(stored, 10, start=8.0, end=2.0) == []


def test_waveform_job_stores_real_peaks(monkeypatch, tmp_path):
    """End to end: the job decodes the upload and the API serves its envelope."""
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    # These modules are reloaded by create_test_client, so they must be bound
    # afterwards and reached through the module: a `from ... import Name` taken
    # earlier would still point at the pre-reload engine and its old database.
    import app.core.config as config
    import app.db.models as models
    import app.db.session as session
    import app.services.jobs as jobs

    write_tone(config.settings.uploads_dir / "tone.wav")
    Job, JobKind, JobStatus, MediaAsset, User = (
        models.Job, models.JobKind, models.JobStatus, models.MediaAsset, models.User
    )
    with session.SessionLocal() as db:
        user = db.query(User).first()
        media = MediaAsset(
            owner_id=user.id,
            original_name="tone.wav",
            stored_name="tone.wav",
            content_type="audio/wav",
            size_bytes=1,
        )
        db.add(media)
        db.commit()
        job = Job(
            owner_id=user.id,
            kind=JobKind.waveform,
            subject_id=media.id,
            status=JobStatus.running,
        )
        db.add(job)
        db.commit()
        jobs._waveform(db, job)
        media_id = media.id
        assert job.status == JobStatus.complete

    payload = client.get(f"/api/media/{media_id}/peaks?buckets=20").json()
    assert payload["ready"] is True
    assert len(payload["peaks"]) == 20
    # The tone occupies the first half of the file and silence the second, and
    # the envelope has to show that difference to be worth drawing.
    assert max(payload["peaks"][:10]) > 0.3
    # The bucket straddling the transition carries the resampler's ring-out, so
    # silence is asserted from the one after it.
    assert max(payload["peaks"][11:]) == 0.0


def test_peaks_endpoint_reports_not_ready_instead_of_failing(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    import app.db.models as models
    import app.db.session as session

    with session.SessionLocal() as db:
        user = db.query(models.User).first()
        media = models.MediaAsset(
            owner_id=user.id,
            original_name="x.wav",
            stored_name="x.wav",
            content_type="audio/wav",
            size_bytes=1,
        )
        db.add(media)
        db.commit()
        media_id = media.id

    payload = client.get(f"/api/media/{media_id}/peaks").json()
    assert payload == {"ready": False, "duration": None, "peaks": []}


def test_peaks_endpoint_rejects_an_absurd_bucket_count(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    import app.db.models as models
    import app.db.session as session

    with session.SessionLocal() as db:
        user = db.query(models.User).first()
        media = models.MediaAsset(
            owner_id=user.id, original_name="x", stored_name="x", content_type="audio/wav", size_bytes=1
        )
        db.add(media)
        db.commit()
        media_id = media.id

    assert client.get(f"/api/media/{media_id}/peaks?buckets=0").status_code == 400
    assert client.get(f"/api/media/{media_id}/peaks?buckets=99999").status_code == 400


def test_upload_queues_a_waveform_job_alongside_analysis(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})

    source = write_tone(tmp_path / "upload.wav", seconds=0.5)
    response = client.post(
        "/api/media/upload",
        files={"file": ("upload.wav", source.read_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    assert {job["kind"] for job in response.json()["jobs"]} == {
        "analyze_media",
        "waveform",
        "transcribe",
    }


# --------------------------------------------------------------------------
# Scene parsing
# --------------------------------------------------------------------------


def test_scene_defaults_when_nothing_is_stored():
    parsed = parse_scene(None, 30.0)
    # Compared against the constants, not a literal: the brand palette is
    # allowed to move without this test needing an edit.
    assert parsed.background == DEFAULT_BACKGROUND
    # The envelope styles are the default: they are the only ones whose
    # proportions we control, so they always fill their box.
    assert parsed.wave_style == "envelope"
    assert parsed.wave_scale == "sqrt"
    # A scene with no layers of its own renders the same default stack the
    # preview shows. It used to render nothing at all: a clip cut by a feed
    # came out as bare captions on a background while Studio showed artwork,
    # a title and a waveform for it.
    assert {layer.type for layer in parsed.layers} >= {"artwork", "waveform", "captions"}
    assert parsed.waveform_layer() is not None


def test_an_explicit_empty_layer_list_is_a_choice():
    """Absent means 'use the defaults'; empty means 'draw nothing'."""
    parsed = parse_scene({"layers": []}, 30.0)
    assert parsed.layers == []


def test_the_default_stack_follows_the_shape():
    """A 16:9 frame's captions start far higher, and the stack moves with them."""
    tall = parse_scene(None, 30.0, "9:16").waveform_layer()
    wide = parse_scene(None, 30.0, "16:9").waveform_layer()
    assert tall is not None and wide is not None
    assert tall.y != wide.y


def test_scene_rejects_unknown_styles_and_bad_colours():
    parsed = parse_scene(
        {"waveStyle": "sparkles", "waveScale": "loud", "background": "red", "accent": "#GGGGGG"},
        10.0,
    )
    assert parsed.wave_style == "envelope"
    assert parsed.wave_scale == "sqrt"
    assert parsed.background == DEFAULT_BACKGROUND
    assert parsed.accent == DEFAULT_ACCENT


def test_layer_timing_outside_the_clip_falls_back_to_the_whole_clip():
    scene = {
        "layers": [
            {"id": "a", "type": "title", "text": "x", "startTime": 8, "endTime": 2},
            {"id": "b", "type": "title", "text": "y", "startTime": 0, "endTime": 999},
        ]
    }
    layers = parse_scene(scene, 10.0).layers
    assert (layers[0].start, layers[0].end) == (8.0, 10.0)
    assert (layers[1].start, layers[1].end) == (0.0, 10.0)


def test_hidden_and_empty_text_layers_are_not_rendered():
    scene = {
        "layers": [
            {"id": "a", "type": "title", "text": "shown", "visible": True},
            {"id": "b", "type": "title", "text": "hidden", "visible": False},
            {"id": "c", "type": "title", "text": "   ", "visible": True},
        ]
    }
    assert [layer.id for layer in parse_scene(scene, 10.0).text_layers()] == ["a"]


def test_scene_ignores_entries_that_are_not_layers():
    scene = {"layers": ["nope", 42, None, {}, {"id": "ok", "type": "title"}]}
    assert [layer.id for layer in parse_scene(scene, 10.0).layers] == ["ok"]


def test_non_numeric_geometry_falls_back_instead_of_crashing():
    scene = {"layers": [{"id": "a", "type": "title", "x": "left", "width": None}]}
    layer = parse_scene(scene, 10.0).layers[0]
    assert layer.x == 0.0
    assert layer.width == 60.0


def test_enable_expression_is_omitted_for_full_length_layers():
    assert enable_expression(0.0, 10.0, 10.0) is None
    assert enable_expression(2.0, 6.0, 10.0) == "between(t,2.000,6.000)"


def test_drawtext_escaping_neutralises_ffmpeg_syntax():
    escaped = escape_drawtext("It's 50%: a\\b")
    assert escaped == r"It\'s 50\%\: a\\b"


def test_ffmpeg_color_prefixes_hex():
    assert ffmpeg_color("#23a094") == "0x23a094"


# --------------------------------------------------------------------------
# Render graph
# --------------------------------------------------------------------------


def render_graph(
    scene: dict,
    duration: float = 12.0,
    font: Path | None = Path("/f.ttf"),
    peaks: list[float] | None = None,
    image_paths: dict | None = None,
    plates=None,
    out_dir: Path | None = None,
) -> str:
    from app.services.jobs import build_render_command

    # Text layers write their words beside the output, so the output has to go
    # somewhere disposable rather than the working directory.
    command = build_render_command(
        Path("s.mp3"), (out_dir or Path(".")) / "o.mp4", "9:16", 0.0, duration,
        scene=scene, font_file=font, peaks=peaks, image_paths=image_paths,
        plates=plates,
    )
    return command[command.index("-filter_complex") + 1]


COVER = {"cover": Path("cover.png")}


# A showwaves style, for the tests that exercise that branch specifically.
LINE_STYLE = {"waveStyle": "line"}


WAVE_LAYER = {
    "id": "wave",
    "type": "waveform",
    "x": 9,
    "y": 50,
    "width": 82,
    "height": 18,
    "color": "#23a094",
    "visible": True,
}


def test_render_places_the_waveform_where_the_editor_put_it():
    graph = render_graph({**LINE_STYLE, "layers": [WAVE_LAYER]})
    # 9% and 50% of a 1080x1920 canvas.
    assert "overlay=x=97:y=960" in graph
    assert "colors=23a094" in graph


def test_render_uses_the_scene_background():
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "9:16", 0.0, 5.0, scene={"background": "#1b1030"}
    )
    assert any("color=c=0x1b1030" in token for token in command)


def test_every_wave_style_produces_a_valid_showwaves_mode():
    valid_modes = {"point", "line", "p2p", "cline"}
    for style in WAVE_STYLES:
        graph = render_graph({"waveStyle": style, "layers": [WAVE_LAYER]})
        mode = graph.split("mode=")[1].split(":")[0]
        assert mode in valid_modes, f"{style} produced mode={mode}"


def test_bar_styles_draw_narrow_and_upscale_with_nearest_neighbour():
    graph = render_graph({"waveStyle": "wideBars", "layers": [WAVE_LAYER]})
    assert "flags=neighbor" in graph
    # 82% of 1080 is 886px; at 18px bars that is a 49px drawing buffer.
    assert "showwaves=s=49x" in graph


def test_line_style_draws_at_full_width_without_upscaling():
    graph = render_graph({"waveStyle": "line", "layers": [WAVE_LAYER]})
    assert "flags=neighbor" not in graph
    assert "showwaves=s=886x" in graph


def test_waveform_is_dropped_when_the_style_is_none():
    graph = render_graph({"waveStyle": "none", "layers": [WAVE_LAYER]})
    assert "showwaves" not in graph
    # The split branch still needs a consumer or the graph is invalid.
    assert "[wavesrc]anullsink" in graph


def test_hidden_waveform_layer_removes_the_wave():
    graph = render_graph({**LINE_STYLE, "layers": [{**WAVE_LAYER, "visible": False}]})
    assert "showwaves" not in graph


def test_text_layers_are_drawn_with_their_timing(tmp_path):
    scene = {
        "layers": [
            {"id": "t", "type": "title", "text": "Hello", "x": 10, "y": 10,
             "width": 80, "height": 7, "startTime": 2, "endTime": 6, "visible": True},
        ]
    }
    graph = render_graph(scene, out_dir=tmp_path)
    assert "drawtext=" in graph
    # Text goes through textfile= because no inline escaping of an apostrophe
    # works; see _text_filters. The words are in the file, not the graph.
    assert "textfile='text-0.txt'" in graph
    assert (tmp_path / "text-0.txt").read_text(encoding="utf-8") == "Hello"
    assert "enable='between(t,2.000,6.000)'" in graph


def test_caption_layers_are_left_to_the_subtitle_filter():
    scene = {"layers": [{"id": "c", "type": "captions", "text": "spoken", "visible": True}]}
    graph = render_graph(scene)
    assert "ass=captions.ass" in graph
    assert "drawtext=" not in graph


def test_text_layers_are_skipped_when_no_font_is_available(monkeypatch):
    """drawtext without a font aborts the whole encode, so the text is dropped."""
    import app.services.jobs as jobs

    monkeypatch.setattr(jobs, "find_font_file", lambda: None)
    scene = {"layers": [{"id": "t", "type": "title", "text": "Hello", "visible": True}]}
    graph = render_graph(scene, font=None)
    assert "drawtext=" not in graph
    assert "ass=captions.ass" in graph


def test_waveform_visual_is_normalised_without_touching_exported_audio():
    """The showwaves branch is normalised; the exported mix stays untouched."""
    graph = render_graph({**LINE_STYLE, "layers": [WAVE_LAYER]})
    wave_branch = next(part for part in graph.split(";") if part.startswith("[wavesrc]"))
    assert "dynaudnorm" in wave_branch
    # Every other branch — in particular the one feeding [aout] — must not be.
    others = [part for part in graph.split(";") if not part.startswith("[wavesrc]")]
    assert not any("dynaudnorm" in part for part in others)


def test_additive_migration_adds_a_missing_column(monkeypatch, tmp_path):
    """A schema addition must not break an installation that already has data."""
    create_test_client(monkeypatch, tmp_path)

    import app.db.init_db as init_db
    import app.db.session as session
    from sqlalchemy import inspect, text

    with session.engine.begin() as connection:
        connection.execute(text('ALTER TABLE "media_assets" DROP COLUMN "peaks_json"'))
    assert "peaks_json" not in {
        column["name"] for column in inspect(session.engine).get_columns("media_assets")
    }

    init_db.add_missing_columns()
    assert "peaks_json" in {
        column["name"] for column in inspect(session.engine).get_columns("media_assets")
    }
    # Running it again must be a no-op rather than an error.
    init_db.add_missing_columns()


# --------------------------------------------------------------------------
# Social clip features: artwork, background, progress, caption presets
# --------------------------------------------------------------------------

ENVELOPE_PEAKS = [0.1, 0.9, 0.4, 1.0, 0.2, 0.7, 0.3, 0.85]


def test_envelope_waveform_draws_bars_from_the_clip_peaks():
    graph = render_graph(
        {"waveStyle": "envelope", "layers": [WAVE_LAYER]}, peaks=ENVELOPE_PEAKS
    )
    # A track pass and a lit pass, one box per bar.
    assert graph.count("drawbox") == 58 * 2
    assert "showwaves" not in graph
    # Bars light as the playhead reaches them, which `enable` evaluates per
    # frame; a width expression would not on older FFmpeg.
    assert "enable='gte(t," in graph


def test_envelope_waveform_needs_peaks_and_degrades_quietly():
    graph = render_graph({"waveStyle": "envelope", "layers": [WAVE_LAYER]}, peaks=[])
    assert "drawbox" not in graph
    # The split branch still needs a consumer or the graph will not build.
    assert "[wavesrc]anullsink" in graph


def test_progress_bar_is_segmented_rather_than_an_expression():
    """drawbox on Debian's FFmpeg resolves geometry once, so segments it is."""
    graph = render_graph(
        {"layers": [{"id": "p", "type": "progress", "x": 8, "y": 92,
                     "width": 84, "height": 1, "visible": True}]}
    )
    assert "eval=frame" not in graph
    assert graph.count("enable='gte(t,") == 96


def test_background_plate_is_blurred_at_low_resolution():
    """Blurring a downscaled copy is both cheaper and softer than a big radius."""
    from app.services.plates import _background_chain

    background = parse_scene(
        {"backgroundImage": {"mediaId": "cover", "blur": 30, "dim": 0.5}}, 10.0
    ).background_image
    chain = _background_chain(background, 1080, 1920)
    assert "scale=180:320" in chain
    assert "boxblur=luma_radius=5" in chain
    assert "eq=brightness=-0.50" in chain


def test_background_plate_without_blur_scales_straight_to_the_canvas():
    from app.services.plates import _background_chain

    background = parse_scene(
        {"backgroundImage": {"mediaId": "cover", "blur": 0}}, 10.0
    ).background_image
    chain = _background_chain(background, 1080, 1920)
    assert "boxblur" not in chain
    assert "scale=1080:1920" in chain


def test_artwork_plate_is_cropped_and_can_be_rounded():
    from app.services.plates import _artwork_chain

    layer = parse_scene(
        {"layers": [{"id": "a", "type": "artwork", "mediaId": "cover",
                     "x": 30, "y": 10, "width": 40, "height": 22.5,
                     "radius": 0.1, "visible": True}]},
        10.0,
    ).image_layers()[0]
    chain = _artwork_chain(layer, 1080, 1920)
    assert "crop=432:432" in chain
    assert "geq=" in chain  # the rounded-corner alpha mask


def test_still_layers_are_composited_not_refiltered_per_frame():
    """The whole point of baking: no scale or blur survives into the main graph."""
    from app.services.plates import Plates

    graph = render_graph(
        {"backgroundImage": {"mediaId": "cover", "blur": 30},
         "layers": [{"id": "a", "type": "artwork", "mediaId": "cover",
                     "x": 30, "y": 10, "width": 40, "height": 22, "visible": True}]},
        plates=Plates(background=Path("bg.png"), artwork={"a": Path("art.png")}),
    )
    assert "boxblur" not in graph
    assert "geq=" not in graph
    assert "overlay=x=0:y=0" in graph
    assert "overlay=x=324:y=192" in graph


def test_artwork_without_a_baked_plate_is_skipped():
    from app.services.jobs import build_render_command
    from app.services.plates import Plates

    command = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "9:16", 0.0, 10.0,
        scene={"layers": [{"id": "a", "type": "artwork", "mediaId": "gone", "visible": True}]},
        plates=Plates(),
    )
    assert "-loop" not in command


def test_image_inputs_are_indexed_after_the_music_track():
    from app.services.jobs import build_render_command
    from app.services.music_bed import MusicBed
    from app.services.plates import Plates

    scene = {
        "backgroundImage": {"mediaId": "cover"},
        "layers": [{"id": "a", "type": "artwork", "mediaId": "cover", "visible": True}],
    }
    command = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "9:16", 0.0, 10.0,
        bed=MusicBed(sound_id="x"), music_path=Path("m.wav"), scene=scene,
        plates=Plates(background=Path("bg.png"), artwork={"a": Path("art.png")}),
    )
    graph = command[command.index("-filter_complex") + 1]
    # 0 source, 1 colour plate, 2 music, 3 background, 4 artwork.
    assert "[2:a]" in graph
    assert "[3:v]" in graph
    assert "[4:v]" in graph


def test_caption_presets_change_size_weight_and_clearance(tmp_path):
    from app.services.jobs import _write_ass

    styles = {}
    for name in ("social", "boxed", "shout", "clean"):
        path = tmp_path / f"{name}.ass"
        _write_ass(path, [{"start": 0, "end": 2, "text": "hook line"}], "9:16",
                   parse_scene({"captionPreset": name}, 10.0))
        styles[name] = next(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Style:")
        ).split(",")

    # Bigger than the understated preset, and bold.
    assert int(styles["shout"][2]) > int(styles["clean"][2])
    assert styles["social"][7] == "-1"
    assert styles["clean"][7] == "0"
    # Every preset must clear the platform's own interface at the bottom.
    for name, fields in styles.items():
        assert int(fields[-2]) > 0, name


def test_shout_preset_uppercases_the_caption(tmp_path):
    from app.services.jobs import _write_ass

    path = tmp_path / "c.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "quiet words"}], "9:16",
               parse_scene({"captionPreset": "shout"}, 10.0))
    assert "QUIET WORDS" in path.read_text(encoding="utf-8")


def test_caption_colour_is_converted_to_ass_bgr(tmp_path):
    from app.services.jobs import _write_ass

    path = tmp_path / "c.ass"
    _write_ass(path, [{"start": 0, "end": 2, "text": "x"}], "9:16",
               parse_scene({"captionColor": "#ffe066"}, 10.0))
    # ASS stores &HAABBGGRR, so #ffe066 becomes 66E0FF.
    assert "&H0066E0FF" in path.read_text(encoding="utf-8")


def test_layer_colour_falls_back_to_the_scene_accent():
    graph = render_graph({**LINE_STYLE, "accent": "#ff8800", "layers": [
        {"id": "w", "type": "waveform", "x": 9, "y": 50, "width": 82,
         "height": 18, "visible": True}
    ]})
    assert "colors=ff8800" in graph


def test_text_layers_default_to_white_not_the_accent():
    """Body copy has to stay readable over artwork; an accent often is not."""
    graph = render_graph({"accent": "#ff8800", "layers": [
        {"id": "t", "type": "title", "text": "Hi", "visible": True}
    ]})
    assert "fontcolor=0xffffff" in graph
