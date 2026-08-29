"""Pulling a catalogue into the library in one go.

The risk in bulk fetching is not the download; it is a non-commercial track
landing in a library that a monetised clip draws from, with the wrong credit
or none. So most of this is about which tracks are refused and what the
manifest says about the ones that are kept.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.cli import fetch_library as fetch


# --------------------------------------------------------------------------
# Licence filtering
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("Attribution", ("CC BY 4.0", True)),
    ("Attribution-ShareAlike", ("CC BY-SA 4.0", True)),
    ("Attribution-ShareAlike 3.0 International", ("CC BY-SA 3.0", True)),
    ("Creative Commons Zero (CC0)", ("CC0 1.0", False)),
    ("Public Domain Mark", ("Public domain", False)),
])
def test_commercially_usable_licences_are_kept(raw, expected):
    assert fetch.fma_licence(raw) == expected


@pytest.mark.parametrize("raw", [
    "Attribution-NonCommercial",
    "Attribution-NonCommercial-ShareAlike",
    "Attribution-NonCommercial-NoDerivatives",
    "Attribution-NoDerivatives",
    "All Rights Reserved",
    "FMA-Limited: Download Only",
    "",
])
def test_everything_else_is_refused(raw):
    """A monetised clip must never carry a NonCommercial track."""
    assert fetch.fma_licence(raw) is None


# --------------------------------------------------------------------------
# Reading the FMA metadata
# --------------------------------------------------------------------------


def metadata_zip(path: Path, rows: list[list[str]]) -> Path:
    """A tracks.csv the way FMA writes it: a three-row header addressed by
    (table, column) pairs, then one row per track with the id first."""
    tables = ["", "track", "track", "track", "track", "artist", "set"]
    columns = ["", "title", "license", "duration", "genre_top", "name", "subset"]
    buffer = io.StringIO()
    import csv

    writer = csv.writer(buffer)
    writer.writerow(tables)
    writer.writerow(columns)
    writer.writerow(["track_id"] + [""] * 6)
    for row in rows:
        writer.writerow(row)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fma_metadata/tracks.csv", buffer.getvalue())
    return path


def test_tracks_are_read_by_column_name_not_position(tmp_path):
    zip_path = metadata_zip(tmp_path / "meta.zip", [
        ["2", "Food", "Attribution-NonCommercial", "168", "Hip-Hop", "AWOL", "medium"],
        ["5", "This World", "Attribution", "206", "Hip-Hop", "AWOL", "medium"],
        ["10", "Freeway", "Creative Commons Zero (CC0)", "161", "Pop", "Kurt Vile", "small"],
    ])
    tracks = fetch.read_fma_tracks(zip_path)
    assert set(tracks) == {5, 10}, "the NonCommercial track got in"
    assert tracks[5]["author"] == "AWOL"
    assert tracks[5]["license"] == "CC BY 4.0"
    assert tracks[5]["needs_credit"] is True
    assert tracks[10]["needs_credit"] is False
    assert tracks[10]["duration_seconds"] == 161.0


# --------------------------------------------------------------------------
# Building the pack
# --------------------------------------------------------------------------


def test_fma_pack_keeps_only_licensed_tracks_and_credits_them(tmp_path, monkeypatch):
    library = tmp_path / "library"
    meta = metadata_zip(tmp_path / "fma_metadata.zip", [
        ["2", "Food", "Attribution-NonCommercial", "168", "Hip-Hop", "AWOL", "medium"],
        ["5", "This World", "Attribution", "206", "Hip-Hop", "AWOL", "medium"],
        ["10", "Freeway", "Creative Commons Zero (CC0)", "161", "Pop", "Kurt Vile", "medium"],
    ])
    audio = tmp_path / "fma_medium.zip"
    with zipfile.ZipFile(audio, "w") as archive:
        for track_id in ("000002", "000005", "000010"):
            archive.writestr(f"fma_medium/000/{track_id}.mp3", b"ID3fake" + track_id.encode())

    # No network: the "downloads" are the files above.
    def fake_download(url, target, expected=None, log=print):
        source = meta if url.endswith("fma_metadata.zip") else audio
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        return target

    monkeypatch.setattr(fetch, "download", fake_download)
    report = fetch.fetch_fma(library, "medium", log=lambda *a: None)

    assert report["kept"] == 2
    assert report["skipped_unlicensed"] == 1
    pack = library / "music" / "fma-medium"
    files = sorted(p.name for p in pack.glob("*.mp3"))
    assert files == ["000005-this-world.mp3", "000010-freeway.mp3"]

    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    by_stem = manifest["tracks"]
    assert by_stem["000005-this-world"]["license"] == "CC BY 4.0"
    assert "AWOL" in by_stem["000005-this-world"]["attribution"]
    assert by_stem["000010-freeway"]["attribution"] == "", "CC0 needs no credit line"
    assert manifest["redistributable"] is False


def test_fma_limit_and_genre_filters(tmp_path, monkeypatch):
    library = tmp_path / "library"
    meta = metadata_zip(tmp_path / "fma_metadata.zip", [
        [str(i), f"Track {i}", "Attribution", "100", "Rock" if i % 2 else "Pop", "A", "medium"]
        for i in range(1, 11)
    ])
    audio = tmp_path / "fma_medium.zip"
    with zipfile.ZipFile(audio, "w") as archive:
        for i in range(1, 11):
            archive.writestr(f"fma_medium/000/{i:06d}.mp3", b"x")
    monkeypatch.setattr(
        fetch, "download",
        lambda url, target, expected=None, log=print: (
            target.parent.mkdir(parents=True, exist_ok=True),
            target.write_bytes((meta if "metadata" in url else audio).read_bytes()),
            target,
        )[2],
    )
    report = fetch.fetch_fma(library, "medium", limit=3, genres={"Rock"}, log=lambda *a: None)
    assert report["kept"] == 3
    manifest = json.loads((library / "music" / "fma-medium" / "pack.json").read_text())
    assert all(t["genre"] == "Rock" for t in manifest["tracks"].values())


# --------------------------------------------------------------------------
# Freesound
# --------------------------------------------------------------------------


def test_freesound_asks_only_for_cc0_and_writes_the_pack(tmp_path, monkeypatch):
    library = tmp_path / "library"
    seen = []

    def fake_request(path, key, **params):
        seen.append(params)
        return {
            "results": [
                {"id": 101, "name": "Big Whoosh", "username": "sam", "license": "CC0",
                 "previews": {"preview-hq-mp3": "https://x/101.mp3"}, "tags": ["whoosh", "swish"],
                 "duration": 1.2, "url": "https://freesound.org/s/101/"},
                {"id": 102, "name": "Small Whoosh", "username": "kim", "license": "CC0",
                 "previews": {"preview-hq-mp3": "https://x/102.mp3"}, "tags": [],
                 "duration": 0.8},
            ],
            "next": None,
        }

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request

    monkeypatch.setattr(fetch, "freesound_request", fake_request)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _Resp(b"MP3DATA"))

    report = fetch.fetch_freesound(library, "KEY", ["whoosh"], per_query=10, log=lambda *a: None)

    assert report["fetched"] == 2
    assert 'license:"Creative Commons 0"' in seen[0]["filter"]
    pack = library / "sfx" / "freesound"
    assert (pack / "101-big-whoosh.mp3").read_bytes() == b"MP3DATA"
    manifest = json.loads((pack / "pack.json").read_text())
    assert manifest["tracks"]["101-big-whoosh"]["license"] == "CC0 1.0"
    assert manifest["tracks"]["101-big-whoosh"]["author"] == "sam"
    assert manifest["tracks"]["101-big-whoosh"]["tags"][0] == "whoosh"


def test_freesound_needs_a_key(tmp_path):
    with pytest.raises(fetch.FetchError, match="API key"):
        fetch.fetch_freesound(tmp_path, "", ["whoosh"])


def test_a_second_freesound_run_adds_to_the_pack_rather_than_replacing_it(tmp_path, monkeypatch):
    library = tmp_path / "library"
    pack = library / "sfx" / "freesound"
    pack.mkdir(parents=True)
    (pack / "pack.json").write_text(json.dumps({"tracks": {
        "1-old": {"title": "Old", "author": "a", "license": "CC0 1.0", "attribution": "", "tags": []},
    }}))
    monkeypatch.setattr(fetch, "freesound_request", lambda *a, **k: {"results": [], "next": None})
    fetch.fetch_freesound(library, "KEY", ["riser"], log=lambda *a: None)
    manifest = json.loads((pack / "pack.json").read_text())
    assert "1-old" in manifest["tracks"]


# --------------------------------------------------------------------------
# Into the catalogue
# --------------------------------------------------------------------------


def test_a_fetched_pack_is_discovered_with_per_track_licences(monkeypatch, tmp_path):
    from tests.test_api import create_test_client

    create_test_client(monkeypatch, tmp_path)

    from app.core.config import settings
    from app.db.models import SoundAsset
    from app.db.session import SessionLocal
    from app.services.library import discover_packs, sync_catalog

    pack = settings.library_dir / "music" / "fma-medium"
    pack.mkdir(parents=True)
    (pack / "000005-this-world.mp3").write_bytes(b"x")
    (pack / "000010-freeway.mp3").write_bytes(b"x")
    fetch.write_manifest(
        pack, name="Free Music Archive (medium)", author="Various artists",
        license_name="Per track", attribution="via FMA", notes="",
        tracks={
            "000005-this-world": {"title": "This World", "author": "AWOL", "license": "CC BY 4.0",
                                  "attribution": '"This World" by AWOL (CC BY 4.0), via Free Music Archive',
                                  "genre": "Hip-Hop", "tags": ["Hip-Hop"], "seamless_loop": False},
            "000010-freeway": {"title": "Freeway", "author": "Kurt Vile", "license": "CC0 1.0",
                               "attribution": "", "genre": "Pop", "tags": ["Pop"], "seamless_loop": False},
        },
    )

    assert "fma-medium" in {p.slug for p in discover_packs()}
    with SessionLocal() as db:
        sync_catalog(db)
        rows = {row.title: row for row in db.query(SoundAsset).all()}
    assert rows["This World"].license_name == "CC BY 4.0"
    assert "AWOL" in rows["This World"].attribution
    assert rows["Freeway"].license_name == "CC0 1.0"
    assert rows["Freeway"].attribution == ""
    assert rows["Freeway"].pack == "fma-medium"
    assert rows["Freeway"].seamless_loop is False
