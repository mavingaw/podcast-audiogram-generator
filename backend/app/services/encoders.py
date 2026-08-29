"""Video encoder selection.

Rendering is the slowest thing this app does, and on a machine with an NVIDIA
card the encode is the part worth moving off the CPU. NVENC is a fixed-function
block, so it also leaves the CPU free for the filter graph — which is where our
work actually is, since every layer is a filter.

Selection is automatic by default and overridable with ``PAS_VIDEO_ENCODER``,
because a shared GPU is sometimes better left alone.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass

_probe_lock = threading.Lock()
_available: set[str] | None = None


@dataclass(frozen=True)
class Encoder:
    name: str
    ffmpeg_name: str
    hardware: bool

    def output_args(self, gpu_index: str | None = None) -> list[str]:
        """Encoder flags tuned for social video: quality-targeted, not bitrate."""
        if self.ffmpeg_name == "h264_nvenc":
            args = [
                "-c:v", "h264_nvenc",
                # p5 is the quality/speed knee on modern NVENC; p7 costs roughly
                # double for a difference nobody sees on a phone.
                "-preset", "p5",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "23",
                # Bitrate 0 hands control entirely to the constant-quality
                # target, which is what keeps a talking-head clip small and a
                # busy one clean.
                "-b:v", "0",
                "-maxrate", "12M",
                "-bufsize", "24M",
                "-pix_fmt", "yuv420p",
            ]
            if gpu_index:
                args += ["-gpu", gpu_index]
            return args
        return [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
        ]


CPU = Encoder(name="cpu", ffmpeg_name="libx264", hardware=False)
NVENC = Encoder(name="nvenc", ffmpeg_name="h264_nvenc", hardware=True)


def available_encoders(refresh: bool = False) -> set[str]:
    """Encoder names this FFmpeg build supports, probed once per process."""
    global _available
    with _probe_lock:
        if _available is not None and not refresh:
            return _available
        try:
            completed = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            _available = {
                line.split()[1]
                for line in completed.stdout.splitlines()
                if len(line.split()) > 1 and line.startswith(" ")
            }
        except (OSError, subprocess.SubprocessError):
            _available = set()
        return _available


def _nvenc_actually_works() -> bool:
    """Encode one frame to confirm a usable device, not just a compiled-in codec.

    A build can list h264_nvenc on a machine with no NVIDIA driver, no card, or
    a card whose session limit is already saturated. Finding that out here costs
    a fraction of a second; finding it out mid-render costs the whole job.
    """
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                "-c:v", "h264_nvenc", "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_verified: bool | None = None


def select(preference: str | None = None) -> Encoder:
    """Choose an encoder. ``auto`` prefers hardware when it is genuinely usable."""
    global _verified
    choice = (preference or os.getenv("PAS_VIDEO_ENCODER") or "auto").strip().lower()

    if choice in {"cpu", "libx264", "x264"}:
        return CPU
    if choice in {"nvenc", "h264_nvenc", "gpu"}:
        return NVENC if "h264_nvenc" in available_encoders() else CPU

    if "h264_nvenc" not in available_encoders():
        return CPU
    with _probe_lock:
        if _verified is None:
            _verified = _nvenc_actually_works()
    return NVENC if _verified else CPU


def describe() -> dict:
    """What the settings screen shows about encoding."""
    encoder = select()
    return {
        "selected": encoder.name,
        "ffmpeg_encoder": encoder.ffmpeg_name,
        "hardware": encoder.hardware,
        "nvenc_available": "h264_nvenc" in available_encoders(),
        "override": os.getenv("PAS_VIDEO_ENCODER", "auto"),
    }
