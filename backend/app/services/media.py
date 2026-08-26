from __future__ import annotations

import json
import subprocess
from pathlib import Path


def ffprobe_media(path: Path) -> tuple[float | None, str | None]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, None

    probe = completed.stdout
    try:
        payload = json.loads(probe)
        duration = payload.get("format", {}).get("duration")
        return (float(duration) if duration else None), probe
    except (ValueError, TypeError):
        return None, probe


def synthetic_transcript(duration: float | None) -> dict:
    total = max(30.0, min(duration or 120.0, 300.0))
    segments = []
    lines = [
        "Welcome to the show. This section is ready for transcript correction and clip selection.",
        "The local workflow keeps the source media, transcript, captions, and renders on your server.",
        "Select a region, choose a format, adjust the scene, then render a shareable audiogram.",
    ]
    cursor = 0.0
    step = min(12.0, total / len(lines))
    for idx, text in enumerate(lines):
        start = cursor
        end = min(total, start + step)
        segments.append({"id": idx + 1, "speaker": "Speaker 1", "start": start, "end": end, "text": text})
        cursor = end
    return {"language": "en", "duration": total, "segments": segments}

