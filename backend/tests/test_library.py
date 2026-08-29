from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from app.services.music_bed import MusicBed, audio_filters, duck_ratio, from_scene
from app.services.song_index import parse_song_index
from tests.test_api import create_test_client

SONG_INDEX = """01 - AMBIENT

001  -  Church Candles by Aibioweapon
Ambient_Church_Candles
Duration: 0:48
< Ambient, Minimal, Ethereal >

002  -  Short Sting
Ambient_Short_Sting
Duration: 0:04 ( Does Not Repeat )
< Ambient, Sting >

02 - CLASSIC

003  -  Toccata and Fugue by Johann Sebastian Bach transcribed by Aibioweapon
Classic_Bach_Toccata_and_Fugue
Duration: 0:41 ( Intro + Loop )
< Classical, Organ >
"""


def write_index(tmp_path: Path) -> Path:
    path = tmp_path / "index.txt"
    path.write_text(SONG_INDEX, encoding="utf-8")
    return path


def write_wav(path: Path, seconds: float = 0.2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(8000 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(frames)))
    return path


# --------------------------------------------------------------------------
# Song index
# --------------------------------------------------------------------------


def test_song_index_recovers_titles_genres_and_tags(tmp_path):
    records = parse_song_index(write_index(tmp_path))

    assert [record.number for record in records] == [1, 2, 3]
    first = records[0]
    assert first.title == "Church Candles"
    assert first.author == "Aibioweapon"
    assert first.genre == "Ambient"
    assert first.duration_seconds == 48
    assert first.tags == ["Ambient", "Minimal", "Ethereal"]


def test_song_index_defaults_the_author_when_the_credit_is_missing(tmp_path):
    records = parse_song_index(write_index(tmp_path))
    assert records[1].title == "Short Sting"
    assert records[1].author == "Aibioweapon"


def test_song_index_marks_one_shot_tracks_as_unloopable(tmp_path):
    records = parse_song_index(write_index(tmp_path))
    assert records[1].seamless_loop is False
    # "Intro + Loop" still loops; only "Does Not Repeat" does not.
    assert records[2].seamless_loop is True
    assert records[2].has_intro is True


def test_song_index_credits_the_transcriber_and_keeps_the_composer(tmp_path):
    records = parse_song_index(write_index(tmp_path))
    assert records[2].author == "Aibioweapon"
    assert records[2].title == "Toccata and Fugue (Johann Sebastian Bach)"
    assert records[2].genre == "Classic"


# --------------------------------------------------------------------------
# Music bed
# --------------------------------------------------------------------------


def test_music_bed_is_absent_without_a_sound_id():
    assert from_scene({}) is None
    assert from_scene({"music": {}}) is None
    assert from_scene(None) is None


def test_music_bed_clamps_out_of_range_levels():
    bed = from_scene({"music": {"soundId": "a", "gainDb": 12, "duckDb": -900}})
    assert bed is not None
    assert bed.gain_db == 0.0
    assert bed.duck_db == -30.0


def test_music_bed_falls_back_on_unparseable_numbers():
    bed = from_scene({"music": {"soundId": "a", "gainDb": "loud"}})
    assert bed is not None
    assert bed.gain_db == -18.0


def test_duck_ratio_grows_with_the_requested_dip():
    assert duck_ratio(0.0) == 1.0
    assert duck_ratio(-12.0) == 7.0
    assert duck_ratio(-100.0) == 20.0


def test_filters_without_music_split_audio_for_the_waveform_only():
    chains, label = audio_filters(MusicBed(sound_id=""), 10.0, has_music_input=False)
    graph = ";".join(chains)
    assert label == "[aout]"
    assert "asplit=2[aout][wavesrc]" in graph
    assert "sidechaincompress" not in graph


def test_filters_with_music_duck_against_the_voice_track():
    bed = MusicBed(sound_id="a", gain_db=-20.0, duck_db=-12.0, fade_in=1.0, fade_out=2.0)
    graph = ";".join(audio_filters(bed, 30.0)[0])
    assert "asplit=3[voice][wavesrc][duckkey]" in graph
    assert "volume=-20.00dB" in graph
    assert "[musicraw][duckkey]sidechaincompress" in graph
    assert "ratio=7.0" in graph
    assert "afade=t=out:st=28.000:d=2.000" in graph
    # The waveform must follow speech, so the bed never reaches showwaves.
    assert "[wavesrc]" in graph


def test_fades_never_exceed_half_the_clip():
    bed = MusicBed(sound_id="a", fade_in=30.0, fade_out=30.0)
    graph = ";".join(audio_filters(bed, 4.0)[0])
    assert "afade=t=in:st=0:d=2.000" in graph
    assert "afade=t=out:st=2.000:d=2.000" in graph


def test_music_is_padded_so_a_short_track_still_spans_the_clip():
    graph = ";".join(audio_filters(MusicBed(sound_id="a"), 45.0)[0])
    assert "apad,atrim=duration=45.000" in graph


def test_ducking_is_bypassed_when_the_dip_is_zero():
    bed = MusicBed(sound_id="a", duck_db=0.0)
    graph = ";".join(audio_filters(bed, 10.0)[0])
    assert "sidechaincompress" not in graph
    assert "[duckkey]anullsink" in graph


# --------------------------------------------------------------------------
# Render command
# --------------------------------------------------------------------------


def test_render_command_loops_the_bed_only_when_asked():
    from app.services.jobs import build_render_command

    looped = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "9:16", 0.0, 10.0,
        MusicBed(sound_id="a", loop=True), Path("m.wav"),
    )
    once = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "9:16", 0.0, 10.0,
        MusicBed(sound_id="a", loop=False), Path("m.wav"),
    )
    assert "-stream_loop" in looped
    assert looped[looped.index("-stream_loop") + 1] == "-1"
    assert "-stream_loop" not in once


def test_render_command_maps_the_mixed_audio_output():
    from app.services.jobs import build_render_command

    command = build_render_command(
        Path("s.mp3"), Path("o.mp4"), "1:1", 2.0, 8.0,
        MusicBed(sound_id="a"), Path("m.wav"),
    )
    maps = [command[i + 1] for i, token in enumerate(command) if token == "-map"]
    assert maps == ["[v]", "[aout]"]
    assert command[command.index("-t") + 1] == "8.000"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@pytest.fixture
def signed_in_client(monkeypatch, tmp_path):
    client = create_test_client(monkeypatch, tmp_path)
    client.post("/api/bootstrap", json={"username": "tester", "password": "long-password"})
    return client


def test_library_endpoints_are_empty_before_any_import(signed_in_client):
    assert signed_in_client.get("/api/library/sounds").json() == {"sounds": []}
    assert signed_in_client.get("/api/library/genres").json() == {"genres": []}
    assert signed_in_client.get("/api/library/sfx").json()["roles"] == {}

    packs = signed_in_client.get("/api/library/packs").json()["packs"]
    assert {pack["slug"] for pack in packs} == {
        "audio-asset-archive",
        "jdsherbert-ultimate-ui-sfx",
    }
    assert all(pack["installed"] is False for pack in packs)
    # Neither pack may be redistributed, so neither is repository content.
    assert all(pack["redistributable"] is False for pack in packs)


def test_sync_registers_imported_tracks_with_their_attribution(signed_in_client, tmp_path):
    import app.core.config as config
    import app.services.library as library

    write_wav(config.settings.library_dir / "music" / "audio-asset-archive" / "Ambient_Church_Candles.wav")
    library.import_music_pack([], write_index(tmp_path))

    result = signed_in_client.post("/api/library/sync").json()["catalog"]
    assert result["total"] == 1

    sounds = signed_in_client.get("/api/library/sounds?kind=music").json()["sounds"]
    assert len(sounds) == 1
    assert sounds[0]["title"] == "Church Candles"
    assert sounds[0]["genre"] == "Ambient"
    assert sounds[0]["attribution"] == "Audio by Aibioweapon"
    assert sounds[0]["tags"] == ["Ambient", "Minimal", "Ethereal"]

    assert signed_in_client.get("/api/library/genres").json() == {"genres": ["Ambient"]}


def test_sound_search_matches_tags_as_well_as_titles(signed_in_client, tmp_path):
    import app.core.config as config
    import app.services.library as library

    directory = config.settings.library_dir / "music" / "audio-asset-archive"
    write_wav(directory / "Ambient_Church_Candles.wav")
    write_wav(directory / "Classic_Bach_Toccata_and_Fugue.wav")
    library.import_music_pack([], write_index(tmp_path))
    signed_in_client.post("/api/library/sync")

    by_tag = signed_in_client.get("/api/library/sounds?search=organ").json()["sounds"]
    assert [sound["title"] for sound in by_tag] == ["Toccata and Fugue (Johann Sebastian Bach)"]

    by_title = signed_in_client.get("/api/library/sounds?search=candles").json()["sounds"]
    assert [sound["title"] for sound in by_title] == ["Church Candles"]


def test_sound_preview_streams_the_file(signed_in_client, tmp_path):
    import app.core.config as config
    import app.services.library as library

    write_wav(config.settings.library_dir / "music" / "audio-asset-archive" / "Ambient_Church_Candles.wav")
    library.import_music_pack([], write_index(tmp_path))
    signed_in_client.post("/api/library/sync")

    sound = signed_in_client.get("/api/library/sounds").json()["sounds"][0]
    response = signed_in_client.get(sound["preview_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"


def test_library_rejects_an_unknown_kind(signed_in_client):
    assert signed_in_client.get("/api/library/sounds?kind=podcast").status_code == 400


def test_stale_rows_are_dropped_when_a_file_leaves_the_library(signed_in_client, tmp_path):
    import app.core.config as config
    import app.services.library as library

    path = config.settings.library_dir / "music" / "audio-asset-archive" / "Ambient_Church_Candles.wav"
    write_wav(path)
    library.import_music_pack([], write_index(tmp_path))
    signed_in_client.post("/api/library/sync")
    assert len(signed_in_client.get("/api/library/sounds").json()["sounds"]) == 1

    path.unlink()
    assert signed_in_client.post("/api/library/sync").json()["catalog"]["removed"] == 1
    assert signed_in_client.get("/api/library/sounds").json()["sounds"] == []
