"""Telling speakers apart.

Captions on a two-hander should say who is talking; a viewer with the sound off
cannot otherwise tell a question from its answer.

## Why this shape

The first attempt at this was dependency-free — numpy over the audio the
transcriber already decodes — and it failed, twice, in ways worth recording
because they rule the approach out rather than merely disappointing:

- **Long-term average spectrum.** Within one speaker, distances of 0.012-0.037;
  between two clearly different speakers, 0.010-0.027. No separation at all.
  Averaged over seconds, the spectrum of speech is dominated by which phonemes
  were said, not by who said them.
- **Fundamental frequency.** Within one speaker, up to 2.44 semitones between
  segments; between two speakers, as little as 1.22. A single person's
  intonation varies more across an episode than two people differ from each
  other. Clustering on it produced four speakers from one person talking.

Telling voices apart needs a model trained to do it. The usual route is pyannote
on PyTorch, which is roughly three gigabytes of dependency and a gated download.
This uses the same pyannote segmentation network and a speaker-embedding network
exported to ONNX and driven by sherpa-onnx: about 46 MB of weights, no PyTorch,
and both models ship inside the image.

## Choosing the number of speakers

Estimating the count is the weak part of this, and the measurements say so
plainly. sherpa's default clustering threshold of 0.5 finds eight speakers in a
reference recording of four. Raising it to 0.9 makes that reference correct —
but on a real single-host podcast episode 0.9 still finds four, and the
threshold that collapses *that* to one (1.1) would merge genuinely different
people elsewhere. There is no single threshold that is right for both.

So the count is asked for rather than guessed. Told there are two speakers, the
same episode splits cleanly and evenly; told there is one, detection is skipped
entirely. For a podcast the number of people in the room is the one thing the
person editing definitely knows, and a question with a certain answer beats an
estimate that is wrong a third of the time.

Automatic estimation remains available for when nobody says, and it is honest
about being the weaker path.
"""

from __future__ import annotations

import logging
import os
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The models are baked into the image. Both are small, and a feature that
# silently does nothing until somebody downloads a file is a feature that
# silently does nothing.
MODELS_DIR = Path(os.getenv("PAS_DIARIZATION_DIR", "/opt/models/diarization"))
SEGMENTATION = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING = MODELS_DIR / os.getenv(
    "PAS_DIARIZATION_EMBEDDING", "nemo_en_titanet_small.onnx"
)

# What the models expect.
SAMPLE_RATE = 16000

# See the module docstring: sherpa's 0.5 finds eight speakers in a recording of
# four. 0.9 is where the estimate becomes right.
CLUSTER_THRESHOLD = float(os.getenv("PAS_DIARIZATION_THRESHOLD", "0.9"))

# Speech shorter than this is not a turn, and silence shorter than this does not
# end one. Both in seconds.
MIN_SPEECH = 0.3
MIN_SILENCE = 0.5

MAX_SPEAKERS = 4


class DiarizationError(RuntimeError):
    pass


@dataclass
class Turn:
    start: float
    end: float
    speaker: int  # one-based


@dataclass
class Diarization:
    turns: list[Turn] = field(default_factory=list)
    speaker_count: int = 1

    def speaker_at(self, start: float, end: float) -> int:
        """Who is speaking across a span, by whichever turn overlaps it most.

        Transcript segments and diarization turns are found by different models
        and do not share boundaries, so a segment is attributed to whoever holds
        the most of it rather than to whatever happens to start first.
        """
        best, best_overlap = 1, 0.0
        for turn in self.turns:
            overlap = min(end, turn.end) - max(start, turn.start)
            if overlap > best_overlap:
                best, best_overlap = turn.speaker, overlap
        return best


def available() -> bool:
    """Whether speaker detection can run."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return SEGMENTATION.exists() and EMBEDDING.exists()


def runtime_status() -> dict:
    try:
        import sherpa_onnx  # noqa: F401

        runtime = True
    except Exception:
        runtime = False
    return {
        "runtime_installed": runtime,
        "segmentation_present": SEGMENTATION.exists(),
        "embedding_present": EMBEDDING.exists(),
        "embedding_model": EMBEDDING.name,
        "threshold": CLUSTER_THRESHOLD,
        "ready": available(),
    }


def _to_wav(source: Path, target: Path) -> None:
    """Decode to the mono 16kHz PCM the models require."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(target),
    ]
    result = subprocess.run(command, capture_output=True, timeout=1800)
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise DiarizationError(f"Could not decode audio: {detail[-1] if detail else '?'}")


def _read(path: Path):
    import numpy as np

    with wave.open(str(path)) as handle:
        if handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 1:
            raise DiarizationError("Audio was not decoded to mono 16kHz")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def analyse(
    path: Path,
    speaker_count: int | None = None,
    on_progress=None,
) -> Diarization:
    """Find who speaks when.

    `speaker_count` of None estimates it, which is the weaker path — pass the
    real number when it is known.
    """
    if speaker_count == 1:
        # Nothing to separate. Skipping is not an optimisation: asked for one
        # speaker, the estimator still ran and returned four, which is exactly
        # the confidently-wrong answer this feature must never give.
        return Diarization(turns=[], speaker_count=1)

    if not available():
        raise DiarizationError("Speaker detection is not installed")

    import sherpa_onnx
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        wav = Path(scratch) / "audio.wav"
        _to_wav(path, wav)
        samples = _read(wav)

        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        clustering = (
            sherpa_onnx.FastClusteringConfig(num_clusters=int(speaker_count))
            if speaker_count and speaker_count > 1
            else sherpa_onnx.FastClusteringConfig(
                num_clusters=-1, threshold=CLUSTER_THRESHOLD
            )
        )
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(SEGMENTATION)
                ),
                num_threads=threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(EMBEDDING), num_threads=threads
            ),
            clustering=clustering,
            min_duration_on=MIN_SPEECH,
            min_duration_off=MIN_SILENCE,
        )
        if not config.validate():
            raise DiarizationError("Speaker detection is misconfigured")

        engine = sherpa_onnx.OfflineSpeakerDiarization(config)
        if on_progress:
            result = engine.process(samples, callback=lambda done, total, _: (
                on_progress(done / total if total else 0.0) or 0
            ))
        else:
            result = engine.process(samples)

    turns_raw = result.sort_by_start_time()
    # sherpa numbers speakers arbitrarily; renumber by who talks first so
    # "Speaker 1" is whoever opened the episode.
    order: dict[int, int] = {}
    turns: list[Turn] = []
    for turn in turns_raw:
        if turn.speaker not in order:
            order[turn.speaker] = len(order) + 1
        turns.append(Turn(start=turn.start, end=turn.end, speaker=order[turn.speaker]))

    # More than a handful is the estimator failing rather than a real crowd, and
    # a caption tinted eleven ways is unreadable regardless.
    if len(order) > MAX_SPEAKERS:
        log.info("Diarization found %s speakers; collapsing to one", len(order))
        return Diarization(turns=[], speaker_count=1)

    return Diarization(turns=turns, speaker_count=max(1, len(order)))


def apply(transcript: dict, result: Diarization) -> dict:
    """Write speaker numbers onto a transcript's segments.

    Names already set by hand are preserved: re-running detection must not undo
    somebody's correction.
    """
    from app.services.speakers import name_for

    for segment in transcript.get("segments") or []:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        number = result.speaker_at(start, end)
        segment["speaker_id"] = number
        segment["speaker"] = name_for(transcript, number)
    transcript["speaker_count"] = result.speaker_count
    return transcript
