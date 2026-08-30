"""Fonts people bring themselves."""

from __future__ import annotations

import struct

import pytest

from app.services import fonts
from tests.test_api import create_test_client


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with create_test_client(monkeypatch, tmp_path) as test_client:
        yield test_client


def login(client) -> None:
    client.post("/api/bootstrap", json={
        "username": "owner", "password": "Passw0rd!enough", "display_name": "Owner",
    })


def tiny_ttf(family: str = "Brand Sans") -> bytes:
    """A minimal sfnt with only a name table naming `family`."""
    name = family.encode("utf-16-be")
    # name table: format 0, 1 record (platform 3, encoding 1, nameID 1).
    records = struct.pack(">6H", 3, 1, 0x409, 1, len(name), 0)
    name_table = struct.pack(">3H", 0, 1, 6 + 12) + records + name
    header = struct.pack(">4sHHHH", b"\x00\x01\x00\x00", 1, 0, 0, 0)
    directory = struct.pack(">4sIII", b"name", 0, len(header) + 16, len(name_table))
    return header + directory + name_table


def test_the_family_name_comes_from_the_font_itself():
    assert fonts.family_from_bytes(tiny_ttf("Adult-Ish Display")) == "Adult-Ish Display"
    assert fonts.family_from_bytes(b"\x00\x01\x00\x00garbage") is None


def test_upload_list_and_delete(client):
    login(client)
    r = client.post("/api/fonts", files={"file": ("brand.ttf", tiny_ttf(), "font/ttf")})
    assert r.status_code == 200, r.text
    font = r.json()
    assert font["family"] == "Brand Sans" and font["id"].startswith("uf-")
    assert client.get("/api/fonts").json()["fonts"] == [{"id": font["id"], "family": "Brand Sans"}]
    assert client.get(f"/api/fonts/{font['id']}/file").status_code == 200
    # Same family twice is a mistake, not a second entry.
    assert client.post("/api/fonts", files={"file": ("brand2.ttf", tiny_ttf(), "font/ttf")}).status_code == 400
    assert client.delete(f"/api/fonts/{font['id']}").status_code == 200
    assert client.get("/api/fonts").json()["fonts"] == []


def test_not_a_font_is_refused(client):
    login(client)
    r = client.post("/api/fonts", files={"file": ("virus.exe", b"MZ pretend", "font/ttf")})
    assert r.status_code == 400 and "not a TTF" in r.json()["detail"]


def test_scene_fonts_resolve_for_the_renderer(client):
    from app.db.session import SessionLocal
    from app.db.models import User
    from app.services import scene as scene_module

    login(client)
    font = client.post("/api/fonts", files={"file": ("brand.ttf", tiny_ttf(), "font/ttf")}).json()
    with SessionLocal() as db:
        user = db.query(User).first()
        fonts.register_scene_fonts(db, user.id, {"font": font["id"], "captionFont": "inter"})
    assert scene_module.font_family_for(font["id"]) == "Brand Sans"
    assert scene_module.font_file_for(font["id"]).name.startswith("uf-")
