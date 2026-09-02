"""Speech transcription with Faster-Whisper.

Captions are the product. On a muted feed they are the only thing carrying the
clip, so a transcript that says "Rendered locally with Kinder"
instead of what the guest actually said makes every export useless. This module
replaces that fixture with real recognition.

The model runs locally — on the GPU when one is usable, otherwise on the CPU —
and weights are cached under ``settings.models_dir`` so a container restart does
not re-download them. Word-level timestamps are requested because caption lines
are only as good as the boundaries they can be split on.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

# Whisper sizes, smallest first. `small` is the default: on a modern GPU it is
# a second or two per minute of audio and its word timings are good enough to
# cut captions on, while `base` mangles names and `medium` upward costs
# download size and memory for gains a caption line rarely shows.
MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3", "distil-large-v3")
DEFAULT_MODEL = "small"

# One line of caption should be readable at a glance on a phone.
MAX_CAPTION_CHARS = 42
MAX_CAPTION_SECONDS = 3.5

_model_lock = threading.Lock()
_model_cache: dict[tuple[str, str, str, int | None], object] = {}


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Runtime:
    device: str
    compute_type: str
    model_size: str
    # Which CUDA device, when a host has more than one and an admin has said
    # which is for transcription. None means "let CTranslate2 pick".
    device_index: int | None = None

    @property
    def on_gpu(self) -> bool:
        return self.device == "cuda"


_dll_paths_added = False


def _register_cuda_libraries() -> None:
    """Put the pip-installed CUDA DLLs on Windows' library search path.

    CTranslate2 loads cuBLAS and cuDNN by name. On Linux the wheels' shared
    objects are found through the loader's normal search; on Windows nothing
    looks inside site-packages, so the GPU path dies with "Library
    cublas64_12.dll is not found" even though the wheel is installed. Adding the
    directories here keeps that plumbing in one place instead of demanding a
    PATH edit from whoever installs this.
    """
    global _dll_paths_added
    if _dll_paths_added or os.name != "nt":
        _dll_paths_added = True
        return
    try:
        import site

        for root in {*site.getsitepackages(), site.getusersitepackages()}:
            nvidia = Path(root) / "nvidia"
            if not nvidia.is_dir():
                continue
            for binary_dir in nvidia.glob("*/bin"):
                if not binary_dir.is_dir():
                    continue
                os.add_dll_directory(str(binary_dir))
                # add_dll_directory only covers Python's own LoadLibraryEx
                # calls. CTranslate2 resolves cuBLAS through the plain search
                # order, which reads PATH, so it needs both.
                if str(binary_dir) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = f"{binary_dir}{os.pathsep}" + os.environ.get("PATH", "")
    except Exception:
        # Worst case the GPU path fails its probe and we fall back to CPU.
        pass
    _dll_paths_added = True


# Run at import: the paths must be in place before CTranslate2 is loaded,
# and `choose_runtime` imports it while probing for a GPU.
_register_cuda_libraries()


def available() -> bool:
    """Whether the transcription stack is installed at all."""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def choose_runtime(
    model_size: str | None = None,
    prefer_gpu: bool = True,
    device_index: int | None = None,
) -> Runtime:
    """Pick a device and precision that this machine can actually run.

    float16 on the GPU is roughly twice as fast as float32 and, for speech
    recognition, the accuracy difference does not survive into a caption line.
    """
    size = (model_size or settings.whisper_model or DEFAULT_MODEL).strip()
    if size not in MODEL_SIZES:
        size = DEFAULT_MODEL

    if prefer_gpu:
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                supported = ctranslate2.get_supported_compute_types("cuda")
                count = ctranslate2.get_cuda_device_count()
                # An index that no longer exists — a card pulled, or the driver
                # reordering them — must not fail the job.
                chosen = device_index if (device_index is not None and 0 <= device_index < count) else None
                for candidate in ("float16", "int8_float16", "float32"):
                    if candidate in supported:
                        return Runtime("cuda", candidate, size, chosen)
        except Exception:
            # A driver mismatch or a busy card is a reason to fall back, not to
            # fail the job.
            pass

    return Runtime("cpu", "int8", size)


def load_model(runtime: Runtime):
    """Load (and cache) a Whisper model.

    Loading costs seconds and a GB of VRAM, and the job worker is single
    threaded, so one cached instance per configuration is both safe and the
    difference between a 4-second transcribe and a 20-second one.
    """
    if not available():
        raise TranscriptionError(
            "faster-whisper is not installed. Add it to requirements.txt "
            "or set PAS_TRANSCRIBE=off to skip transcription."
        )
    from faster_whisper import WhisperModel

    key = (runtime.model_size, runtime.device, runtime.compute_type, runtime.device_index)
    with _model_lock:
        if key not in _model_cache:
            settings.models_dir.mkdir(parents=True, exist_ok=True)
            try:
                kwargs = {}
                if runtime.device_index is not None:
                    kwargs["device_index"] = runtime.device_index
                if runtime.device == "cpu":
                    # CTranslate2 defaults to 4 threads whatever the host has,
                    # which leaves most of a big machine idle on the fallback
                    # path. Half the logical cores keeps room for a render.
                    kwargs["cpu_threads"] = max(4, (os.cpu_count() or 8) // 2)
                _model_cache[key] = WhisperModel(
                    runtime.model_size,
                    device=runtime.device,
                    compute_type=runtime.compute_type,
                    download_root=str(settings.models_dir),
                    **kwargs,
                )
            except Exception as error:
                raise TranscriptionError(
                    f"Could not load the {runtime.model_size} model on "
                    f"{runtime.device}: {error}"
                ) from error
        return _model_cache[key]


def transcribe(
    path: Path,
    language: str | None = None,
    model_size: str | None = None,
    prefer_gpu: bool = True,
    device_index: int | None = None,
    on_progress=None,
) -> dict:
    """Transcribe a media file into this project's transcript shape.

    ``language`` of ``None`` lets Whisper detect it. ``on_progress`` is called
    with a 0..1 fraction so a long episode can report movement rather than
    appearing hung.
    """
    runtime = choose_runtime(model_size, prefer_gpu, device_index)
    model = load_model(runtime)

    # Decoded by ffmpeg first, not handed to the model as-is. The model's own
    # decoder (PyAV) raises on the first damaged frame and *stops*, and the
    # result looks like success: a 58-minute episode came back as nine
    # minutes of transcript and "Transcript ready". ffmpeg logs the bad frame
    # and carries on, which is what every player does with the same file.
    with decoded_for_whisper(path) as audio_path:
        return _transcribe_decoded(audio_path, model, runtime, language, on_progress)


@contextmanager
def decoded_for_whisper(path: Path):
    """A 16 kHz mono WAV of `path`, however damaged the original is.

    Falls back to the original file when ffmpeg is missing or cannot open
    it, so nothing that transcribed before stops transcribing now.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pas-whisper-") as tmp:
        wav = Path(tmp) / "audio.wav"
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-err_detect", "ignore_err", "-i", str(path),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                capture_output=True, text=True, timeout=1800,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("ffmpeg unavailable for transcription decode: %s", error)
            yield path
            return
        if result.returncode != 0 or not wav.exists() or wav.stat().st_size < 1024:
            log.warning("ffmpeg could not decode %s for transcription: %s", path.name,
                        (result.stderr or "").strip()[-200:])
            yield path
            return
        yield wav


def _transcribe_decoded(path: Path, model, runtime, language, on_progress) -> dict:
    try:
        segments, info = model.transcribe(
            str(path),
            language=language or None,
            word_timestamps=True,
            # Silence between speakers is most of a podcast's dead air; skipping
            # it is the single biggest speed win and it stops Whisper
            # hallucinating filler into the gaps.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
            condition_on_previous_text=False,
        )
    except Exception as error:
        raise TranscriptionError(f"Transcription failed: {error}") from error

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    collected = []
    for index, segment in enumerate(segments, start=1):
        words = [
            {
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                # Not stripped: the leading space Whisper attaches is how
                # hyphenation and punctuation stay correct when rejoined.
                "text": word.word,
            }
            for word in (segment.words or [])
            if word.start is not None and word.end is not None
        ]
        collected.append(
            {
                "id": index,
                "speaker": "Speaker 1",
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "words": words,
            }
        )
        if on_progress and duration:
            on_progress(min(1.0, float(segment.end) / duration))

    result = {
        "language": getattr(info, "language", None) or language or "en",
        "duration": duration,
        "model": runtime.model_size,
        "device": runtime.device,
        "device_index": runtime.device_index,
        "segments": collected,
    }
    # A transcript that ends long before the audio does is not "ready"; say
    # so, so the person is not left wondering why the captions stop.
    reached = float(collected[-1]["end"]) if collected else 0.0
    if duration > 120 and reached < duration * 0.8:
        result["warnings"] = [
            f"The words stop at {int(reached // 60)}:{int(reached % 60):02d} of "
            f"{int(duration // 60)}:{int(duration % 60):02d}. The rest of the audio "
            "may be silence, music, or damaged."
        ]
    return result


# Punctuation that belongs to the word before it, so no space is inserted.
_ATTACHING = ",.!?;:%)]}'’”-"
_OPENING = "([{‘“"


def join_words(words: list[dict]) -> str:
    """Rebuild a line from word tokens without inventing spaces.

    Whisper emits tokens carrying their own leading space, and hyphenated
    speech arrives as "day", "-to", "-day". Joining those on " " produced
    "day -to -day" in a burned-in caption. This respects a token's own spacing
    where it has any, and otherwise attaches punctuation the way a reader
    expects.
    """
    out = ""
    for word in words:
        token = str(word.get("text", ""))
        if not token:
            continue
        if not out:
            out = token.lstrip()
            continue
        if token[:1].isspace() or token.lstrip()[:1] in _ATTACHING or out[-1:] in _OPENING:
            out += token if token[:1].isspace() else token.lstrip()
        else:
            out += " " + token.lstrip()
    return out.strip()


def edited_words(segment: dict) -> list[dict]:
    """The segment's words, honouring a line somebody retyped.

    The transcript editor changes a segment's `text`; the word timings stay
    as Whisper heard them. Captions are built from the words, so a corrected
    spelling never reached the video — the transcript said one thing and the
    burned-in captions another. When the retyped line has as many tokens as
    there are words, each token takes its word's timing. When it does not,
    the timings cannot be trusted to line up, so the line goes out whole
    (no per-word highlight), which is still the right words.
    """
    words = segment.get("words") or []
    text = str(segment.get("text", "")).strip()
    if not words or not text or join_words(words) == text:
        return words
    tokens = text.split()
    if len(tokens) == len(words):
        return [{**word, "text": token} for word, token in zip(words, tokens)]
    return []


def caption_lines(
    transcript: dict, start: float, end: float, max_chars: int | None = None
) -> list[dict]:
    """Split a transcript into caption-sized lines over a time window.

    Whisper segments run to whole sentences, which are far too long to burn in
    at social caption sizes. Word timings let us break on the word that crosses
    either limit, so a line never runs past the frame or lingers after it was
    spoken.
    """
    budget = max_chars or MAX_CAPTION_CHARS
    lines: list[dict] = []
    for segment in transcript.get("segments", []):
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", seg_start))
        if seg_end <= start or seg_start >= end:
            continue

        words = edited_words(segment)
        if not words:
            text = str(segment.get("text", "")).strip()
            if text:
                line = {
                    "start": max(0.0, seg_start - start),
                    "end": max(0.3, min(seg_end, end) - start),
                    "text": text,
                }
                if segment.get("speaker_id") is not None:
                    line["speaker_id"] = segment["speaker_id"]
                lines.append(line)
            continue

        visible = [
            word
            for word in words
            if float(word.get("end", 0.0)) > start and float(word.get("start", 0.0)) < end
        ]
        speaker = segment.get("speaker_id")
        for group in _balanced_groups(visible, budget):
            line = _line(group, start, end)
            # Carried so the renderer can tint the line without re-reading the
            # transcript and re-deriving which segment it came from.
            if speaker is not None:
                line["speaker_id"] = speaker
            lines.append(line)

    return [line for line in lines if line["text"]]


def _balanced_groups(words: list[dict], budget: int = MAX_CAPTION_CHARS) -> list[list[dict]]:
    """Split words into caption lines of roughly equal length.

    Packing greedily up to the character limit leaves whatever is left over on
    a line of its own, which is how a caption ends up reading "I" for a
    hundredth of a second. Deciding the line count first and then aiming for an
    even share means the last line is as full as the rest.
    """
    if not words:
        return []

    text_length = len(join_words(words))
    span = float(words[-1].get("end", 0.0)) - float(words[0].get("start", 0.0))
    line_count = max(
        1,
        -(-text_length // budget),  # ceil
        -(-int(span) // int(MAX_CAPTION_SECONDS)) if span > MAX_CAPTION_SECONDS else 1,
    )
    target = text_length / line_count

    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        candidate = current + [word]
        # Measured the way it will actually be rendered, so the budget means
        # what it says.
        length = len(join_words(candidate))
        # A line must never begin with a token that attaches to the one before
        # it. Breaking between "day-to" and "-day" put a stray hyphen at the
        # start of a caption.
        attaches = str(word.get("text", "")).lstrip()[:1] in _ATTACHING
        # Close the line once adding another word would take it further past
        # the target than stopping here leaves it short.
        if current and not attaches and len(groups) < line_count - 1:
            without = len(join_words(current))
            if abs(length - target) > abs(without - target):
                groups.append(current)
                current = [word]
                continue
        current = candidate
    if current:
        groups.append(current)
    return groups


def _line(words: list[dict], start: float, end: float) -> dict:
    first = float(words[0].get("start", start))
    last = float(words[-1].get("end", first))
    # The words travel with the line so captions can be highlighted one word at
    # a time. Timings are made relative to the clip here, like the line's own,
    # so nothing downstream has to know where the clip began.
    spans = []
    for word in words:
        token = join_words([word])
        if not token:
            continue
        spans.append({
            "start": max(0.0, float(word.get("start", first)) - start),
            "end": max(0.0, float(word.get("end", last)) - start),
            "text": token,
        })
    return {
        "start": max(0.0, first - start),
        "end": max(0.3, min(last, end) - start),
        "text": join_words(words),
        "words": spans,
    }
