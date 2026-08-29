"""Waveform peak extraction.

The editor used to draw a decorative sine-like pattern that had nothing to do
with the audio, which made clip selection guesswork. This module decodes the
source with FFmpeg and stores a real peak envelope on the media asset.

Peaks are kept at a fixed 10 buckets per second as unsigned bytes, base64
encoded: a one-hour episode costs about 48 KB of text instead of the ~250 KB the
same data would take as a JSON float array, and the resolution is still four
times finer than any pixel column the browser will draw. Callers ask for the
bucket count they can actually display and :func:`resample` reduces to it.

Like the reference application, generation is a tracked asynchronous job rather
than something the upload handler waits on.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile

from app.services import cancellation
from pathlib import Path

# Buckets per second of source audio.
PEAK_RATE_HZ = 10
# Decode rate: high enough that a bucket's maximum is a true peak, low enough
# that a long episode stays a few megabytes of PCM.
DECODE_RATE_HZ = 4000
MAX_BUCKETS = 4000


class WaveformError(RuntimeError):
    pass


def extract_peaks(path: Path, timeout: int = 900, job_id: str | None = None) -> dict:
    """Decode ``path`` and return its peak envelope.

    Returns a dict ready to be stored as ``MediaAsset.peaks_json``::

        {"version": 1, "rate": 10, "count": 1234, "duration": 123.4, "peaks": "<base64>"}
    """
    # Decode to a temp file rather than to a pipe. Writing PCM to stdout works
    # from a terminal but fails inside a server process whose own stdout has
    # been redirected — FFmpeg reports "Error opening output files: Invalid
    # argument" and the job dies. A file has no such dependency, and 28 MB for
    # an hour of audio is a fair trade for a decode path that behaves the same
    # in a shell, under uvicorn, and in a container.
    with tempfile.TemporaryDirectory(prefix="pas-peaks-") as scratch:
        target = Path(scratch) / "audio.raw"
        try:
            completed = cancellation.run(
                job_id,
                [
                    "ffmpeg",
                    "-v", "error",
                    "-nostdin",
                    "-i", str(path),
                    "-ac", "1",
                    "-ar", str(DECODE_RATE_HZ),
                    "-f", "s16le",
                    "-y", str(target),
                ],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise WaveformError("Waveform extraction timed out")
        except FileNotFoundError:
            raise WaveformError("FFmpeg is not installed or not on PATH")

        if completed.returncode != 0:
            # Keep the whole of FFmpeg's complaint: the last line is usually a
            # generic summary, and the useful cause is a line or two above it.
            detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
            raise WaveformError(
                f"FFmpeg could not decode the media (exit {completed.returncode}) "
                f"writing to {target}: {detail or 'no output'}"
            )
        if not target.exists() or target.stat().st_size == 0:
            raise WaveformError("Media contains no decodable audio")

        return _envelope(target.read_bytes())


def _envelope(raw: bytes) -> dict:
    """Reduce signed 16-bit mono PCM to one unsigned byte per bucket."""
    import array

    samples = array.array("h")
    # An odd trailing byte would desynchronise the whole array.
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys_is_big_endian():
        samples.byteswap()

    per_bucket = max(1, DECODE_RATE_HZ // PEAK_RATE_HZ)
    buckets = bytearray()
    for offset in range(0, len(samples), per_bucket):
        window = samples[offset : offset + per_bucket]
        if not window:
            break
        loudest = max(max(window), -min(window))
        buckets.append(min(255, (loudest * 255) // 32768))

    return {
        "version": 1,
        "rate": PEAK_RATE_HZ,
        "count": len(buckets),
        "duration": len(buckets) / PEAK_RATE_HZ,
        "peaks": base64.b64encode(bytes(buckets)).decode("ascii"),
    }


def sys_is_big_endian() -> bool:
    import sys

    return sys.byteorder == "big"


def decode(stored: str | None) -> list[int] | None:
    """Return the raw bucket values from a stored envelope, or ``None``."""
    if not stored:
        return None
    try:
        payload = json.loads(stored)
        return list(base64.b64decode(payload["peaks"]))
    except (ValueError, KeyError, TypeError):
        return None


def resample(
    stored: str | None,
    buckets: int,
    start: float | None = None,
    end: float | None = None,
) -> list[float]:
    """Reduce a stored envelope to ``buckets`` values in the range 0..1.

    ``start``/``end`` select a time window, which is what lets the clipper zoom
    into a selection without the backend re-decoding anything.
    """
    values = decode(stored)
    if not values:
        return []

    payload = json.loads(stored or "{}")
    rate = float(payload.get("rate") or PEAK_RATE_HZ)

    first = 0 if start is None else max(0, int(start * rate))
    last = len(values) if end is None else min(len(values), int(end * rate))
    if last <= first:
        return []
    window = values[first:last]

    buckets = max(1, min(buckets, MAX_BUCKETS))
    if buckets >= len(window):
        return [value / 255 for value in window]

    # Peak-preserving downsample: a quiet average would erase transients, which
    # are exactly the landmarks someone uses to find a clip boundary.
    step = len(window) / buckets
    out = []
    for index in range(buckets):
        chunk = window[int(index * step) : max(int((index + 1) * step), int(index * step) + 1)]
        out.append((max(chunk) if chunk else 0) / 255)
    return out


def duration_of(stored: str | None) -> float | None:
    if not stored:
        return None
    try:
        return float(json.loads(stored).get("duration"))
    except (ValueError, TypeError, AttributeError):
        return None
