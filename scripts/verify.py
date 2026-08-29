"""End-to-end verification against a running instance.

Exercises every connection the product depends on, in the order a real session
uses them: bootstrap, sign-in, upload, background jobs, waveform peaks, project
edit, music library, render, and download. Run it against a dev server or
against the container — it only speaks HTTP.

    python scripts/verify.py --base-url http://localhost:8080

Exit code 0 means every check passed. Anything else is a failure with the
offending response printed.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

TIMEOUT = 120


class Failure(RuntimeError):
    pass


class Client:
    """A urllib session that remembers the auth cookie — no dependencies."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        raw: tuple[bytes, str] | None = None,
        expect: int = 200,
    ):
        url = f"{self.base}{path}"
        data, content_type = None, None
        if body is not None:
            data = json.dumps(body).encode()
            content_type = "application/json"
        elif raw is not None:
            data, content_type = raw

        request = urllib.request.Request(url, data=data, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        if self.cookie:
            request.add_header("Cookie", self.cookie)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                status, payload, headers = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, payload, headers = error.code, error.read(), error.headers

        set_cookie = headers.get("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]

        if status != expect:
            raise Failure(
                f"{method} {path} -> {status} (expected {expect})\n"
                f"{payload[:400].decode('utf-8', 'replace')}"
            )
        if headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(payload)
        return payload


def tone(seconds: float = 6.0, rate: int = 16000) -> bytes:
    """A wav with a loud first half and a silent second, so peaks are checkable."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = []
        for index in range(int(rate * seconds)):
            loud = index < rate * seconds / 2
            value = int(0.6 * 32767 * math.sin(index * 0.06)) if loud else 0
            frames.append(struct.pack("<h", value))
        handle.writeframes(b"".join(frames))
    return buffer.getvalue()


def png_pixel() -> bytes:
    """A minimal valid 1x1 PNG, for the artwork upload path."""
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x88\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def multipart(field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----pas{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def await_job(client: Client, job_id: str, label: str, limit: int = 180) -> dict:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        job = client.request("GET", f"/api/jobs/{job_id}")["job"]
        if job["status"] == "complete":
            return job
        if job["status"] in {"failed", "canceled"}:
            raise Failure(f"{label} job {job['status']}: {job.get('error')}")
        time.sleep(1)
    raise Failure(f"{label} job did not finish within {limit}s")


CHECKS: list[str] = []


def check(name: str):
    def wrap(fn):
        CHECKS.append(name)
        return fn

    return wrap


def run(base_url: str, username: str, password: str) -> int:
    client = Client(base_url)
    passed: list[str] = []

    def ok(name: str, detail: str = "") -> None:
        passed.append(name)
        print(f"  PASS  {name}{f'  ({detail})' if detail else ''}")

    print(f"Verifying {base_url}\n")

    # -- Health and static shell ------------------------------------------
    health = client.request("GET", "/api/health")
    if not health.get("ok"):
        raise Failure(f"health endpoint not ok: {health}")
    ok("health", health.get("app", ""))

    index = client.request("GET", "/")
    if b"<div id=\"root\"" not in index and b"<script" not in index:
        raise Failure("frontend bundle is not being served at /")
    ok("frontend served", f"{len(index)} bytes")

    # -- Auth --------------------------------------------------------------
    state = client.request("GET", "/api/bootstrap")
    if state["initialized"]:
        client.request("POST", "/api/auth/login", {"username": username, "password": password})
        ok("sign-in")
    else:
        client.request("POST", "/api/bootstrap", {"username": username, "password": password})
        ok("bootstrap admin", username)
        print(f"        NOTE: {username} is now a real administrator on this instance.")
        print("        Remove it, or change its password, before this box is exposed.")

    me = client.request("GET", "/api/me")["user"]
    ok("session", me["username"])

    client.request("GET", "/api/media/none/peaks", expect=404)
    ok("unknown media rejected")

    # -- Upload and background jobs ---------------------------------------
    upload = client.request(
        "POST", "/api/media/upload", raw=multipart("file", "verify.wav", tone(), "audio/wav")
    )
    media_id = upload["media"]["id"]
    kinds = {job["kind"] for job in upload["jobs"]}
    if kinds != {"analyze_media", "waveform", "transcribe"}:
        raise Failure(f"unexpected jobs queued: {kinds}")
    ok("audio upload", media_id[:8])

    for job in upload["jobs"]:
        await_job(client, job["id"], job["kind"])
    ok("background jobs", ", ".join(sorted(kinds)))

    # -- Waveform peaks ----------------------------------------------------
    peaks = client.request("GET", f"/api/media/{media_id}/peaks?buckets=40")
    if not peaks["ready"] or len(peaks["peaks"]) != 40:
        raise Failure(f"peaks not ready or wrong length: {peaks['ready']}, {len(peaks['peaks'])}")
    if max(peaks["peaks"][:18]) < 0.2:
        raise Failure("peaks do not reflect the loud half of the source")
    if max(peaks["peaks"][22:]) > 0.05:
        raise Failure("peaks do not reflect the silent half of the source")
    ok("waveform peaks", f"max {max(peaks['peaks']):.2f}")

    windowed = client.request("GET", f"/api/media/{media_id}/peaks?buckets=10&start=3.5&end=6")
    if max(windowed["peaks"]) > 0.05:
        raise Failure("windowed peaks ignored the time range")
    ok("peak windowing")

    client.request("GET", f"/api/media/{media_id}/peaks?buckets=0", expect=400)
    ok("peak bounds validated")

    # -- Artwork upload ----------------------------------------------------
    cover = client.request(
        "POST", "/api/media/upload", raw=multipart("file", "cover.png", png_pixel(), "image/png")
    )
    cover_id = cover["media"]["id"]
    # Artwork has no audio stream, so audio jobs must not be queued for it.
    if cover["jobs"]:
        raise Failure(f"image upload queued audio jobs: {[j['kind'] for j in cover['jobs']]}")
    ok("image upload", cover_id[:8])

    client.request(
        "POST", "/api/media/upload",
        raw=multipart("file", "bad.txt", b"nope", "text/plain"),
        expect=415,
    )
    ok("bad upload rejected")

    # -- Sound library -----------------------------------------------------
    packs = client.request("GET", "/api/library/packs")["packs"]
    if any(pack["redistributable"] for pack in packs):
        raise Failure("a pack is marked redistributable; it must not ship in the image")
    sounds = client.request("GET", "/api/library/sounds?kind=music&limit=5")["sounds"]
    ok("sound library", f"{len(sounds)} track(s), {len(packs)} pack(s)")

    bed = None
    if sounds:
        bed = sounds[0]
        audio = client.request("GET", bed["preview_url"])
        if len(audio) < 100:
            raise Failure("sound preview returned no audio")
        ok("sound preview", bed["title"])
        genres = client.request("GET", "/api/library/genres")["genres"]
        ok("library genres", f"{len(genres)}")

    # -- Project -----------------------------------------------------------
    project = client.request(
        "POST", "/api/projects", {"title": "Verification clip", "media_id": media_id}
    )["project"]

    scene = {
        "background": "#14100f",
        "accent": "#ffe066",
        "waveStyle": "envelope",
        "captionPreset": "social",
        "captionColor": "#ffffff",
        "backgroundImage": {"mediaId": cover_id, "blur": 24, "dim": 0.5},
        "layers": [
            {"id": "art", "type": "artwork", "mediaId": cover_id, "x": 30, "y": 12,
             "width": 40, "height": 22.5, "radius": 0.1, "visible": True},
            {"id": "wave", "type": "waveform", "x": 10, "y": 45, "width": 80,
             "height": 10, "visible": True},
            {"id": "title", "type": "title", "x": 6, "y": 36, "width": 88,
             "height": 3.4, "text": "Verification", "visible": True},
            {"id": "prog", "type": "progress", "x": 8, "y": 94, "width": 84,
             "height": 0.7, "visible": True},
        ],
    }
    if bed:
        scene["music"] = {"soundId": bed["id"], "gainDb": -22, "duckDb": -14, "loop": True}

    updated = client.request(
        "PATCH", f"/api/projects/{project['id']}",
        {"clip_start": 0.5, "clip_end": 4.5, "aspect_ratio": "9:16", "scene": scene},
    )["project"]
    if updated["clip_end"] != 4.5 or updated["scene"]["waveStyle"] != "envelope":
        raise Failure("project update did not persist")
    ok("project scene saved", project["id"][:8])

    reread = client.request("GET", f"/api/projects/{project['id']}")["project"]
    if reread["scene"]["backgroundImage"]["mediaId"] != cover_id:
        raise Failure("scene did not survive a re-read")
    ok("scene survives reload")

    # -- Render and download ----------------------------------------------
    render = client.request("POST", f"/api/projects/{project['id']}/render", {})["job"]
    finished = await_job(client, render["id"], "render", limit=600)
    downloads = (finished.get("result") or {}).get("downloads", {})
    if "mp4" not in downloads:
        raise Failure(f"render produced no mp4: {finished}")
    ok("render", finished["message"])

    mp4 = client.request("GET", downloads["mp4"])
    if len(mp4) < 20000 or mp4[4:8] != b"ftyp":
        raise Failure(f"mp4 download looks wrong: {len(mp4)} bytes, magic {mp4[4:8]!r}")
    ok("mp4 download", f"{len(mp4) // 1024} KB")

    for name in ("srt", "vtt", "manifest"):
        if name in downloads:
            client.request("GET", downloads[name])
    ok("caption and manifest downloads")

    if not sounds:
        print("  SKIP  sound library is empty; import a pack to exercise attribution")
    if bed:
        if "credits" not in downloads:
            raise Failure("a licensed track was used but no credits file was produced")
        credits = client.request("GET", downloads["credits"]).decode("utf-8", "replace")
        if bed["author"] not in credits:
            raise Failure("credits file does not name the track's author")
        ok("attribution written", bed["author"])

    # The endpoint serves application/json, so the client already parsed it.
    manifest = client.request("GET", downloads["manifest"])
    if manifest["scene"]["captionPreset"] != "social":
        raise Failure("render manifest lost the scene")
    ok("render manifest")

    client.request("GET", f"/api/projects/{project['id']}/outputs/../../secrets", expect=404)
    ok("output path traversal blocked")

    # -- Templates ---------------------------------------------------------
    # A saved look must carry the design and none of the episode, or applying
    # last week's template puts last week's audio on this week's clip.
    saved = client.request(
        "POST", "/api/templates",
        {"name": "Verification look", "project_id": project["id"]},
    )["template"]
    if any("mediaId" in layer for layer in saved["scene"].get("layers", [])):
        raise Failure("template kept a layer's media")
    if "music" in saved["scene"]:
        raise Failure("template kept the episode's music bed")
    ok("template saved", saved["name"])

    applied = client.request(
        "POST", f"/api/projects/{project['id']}/template/{saved['id']}", {}
    )["project"]
    if applied["scene"].get("captionPreset") != saved["scene"].get("captionPreset"):
        raise Failure("applying a template did not carry the design")
    ok("template applied")

    client.request("DELETE", f"/api/templates/{saved['id']}")
    if any(t["id"] == saved["id"] for t in client.request("GET", "/api/templates")["templates"]):
        raise Failure("deleted template is still listed")
    ok("template deleted")

    # -- Cleanup -----------------------------------------------------------
    # Remove the project this run created. Without this every verification run
    # leaves one behind, and a workspace fills up with "Verification clip".
    client.request("DELETE", f"/api/projects/{project['id']}")
    if any(p["id"] == project["id"] for p in client.request("GET", "/api/projects")["projects"]):
        raise Failure("the verification project was not cleaned up")
    ok("verification project removed")

    failed = [j for j in client.request("GET", "/api/jobs")["jobs"] if j["status"] == "failed"]
    if failed:
        raise Failure(
            "jobs failed during the run: "
            + "; ".join(f"{j['kind']}: {j['error']}" for j in failed)
        )
    ok("no failed jobs")

    client.request("POST", "/api/auth/logout", {})
    client.request("GET", "/api/me", expect=401)
    ok("sign-out")

    print(f"\n{len(passed)} checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--username", default="verifier")
    # No default. Bootstrapping an empty instance creates a *real admin
    # account*, and a default here means running this against a reachable box
    # silently leaves an administrator behind whose password is in the script.
    parser.add_argument(
        "--password",
        help="Required. Used to sign in, or to bootstrap the first admin on an "
             "empty instance. Pick something you would be happy to leave behind, "
             "because on an empty instance that is exactly what happens.",
    )
    args = parser.parse_args(argv)
    if not args.password:
        parser.error(
            "--password is required: on an empty instance this bootstraps a "
            "real admin account, so the credential must be a deliberate choice"
        )

    try:
        return run(args.base_url, args.username, args.password)
    except Failure as failure:
        print(f"\n  FAIL  {failure}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"\n  FAIL  cannot reach {args.base_url}: {error.reason}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
