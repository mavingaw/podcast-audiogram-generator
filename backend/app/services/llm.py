"""A local language model, for judging what a clip is actually about.

The heuristics in `clipfinder` measure *form*: length, punctuation, pace, filler
density, audio energy. They cannot tell whether a passage is worth watching. Two
stretches of speech can score identically on every one of those signals while
one is the best thing in the episode and the other is the host explaining where
to find the show notes. Closing that gap needs something that reads.

Design constraints, in the order that decided the shape of this:

**The weights are not in the image.** A packaged model would add five gigabytes
to every deploy. It is fetched once, on first use, into the same models volume
the Whisper weights already live in, and cached there forever.

**It runs in-process, not as a second service.** llama.cpp through
`llama-cpp-python` loads a quantised model directly, so there is no sidecar
container, no port, and no second thing that can be down.

**Absence is normal.** No wheel, no weights, no GPU, no network — every one of
those is a supported state. The heuristic ranking is always the answer; this
only ever re-orders it. Nothing here may raise into a caller.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

# Qwen2.5 7B Instruct at Q4_K_M.
#
# Chosen for this job rather than in general. It has to read a paragraph of
# conversational speech and return strict JSON, and instruction-following at
# that size is what decides whether the output parses — a larger model that
# rambles is worthless here. Q4_K_M is the usual quality knee: about 4.7GB, so
# it sits alongside Whisper on a 16GB card with room to spare, and a 4090
# scores an episode's shortlist in a couple of seconds.
#
# Override with PAS_LLM_MODEL / PAS_LLM_MODEL_URL to run something else; nothing
# below is specific to this model beyond the chat formatting llama.cpp derives
# from the GGUF itself.
DEFAULT_MODEL = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
# Qwen's own GGUF repo splits this quant across two shards, which needs a
# multi-file fetch; the single-file build is the same weights in one download.
DEFAULT_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/"
    "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
)

# Enough for the passage plus a short JSON answer.
CONTEXT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 220
# How many heuristic candidates are worth reading. The point of the heuristic
# pass is that the model never has to look at the whole episode.
DEFAULT_SHORTLIST = 12

_lock = threading.Lock()
_model = None
_load_failed = False


class LlmUnavailable(RuntimeError):
    """Raised internally when the model cannot be used. Never escapes a caller."""


# Where the image bakes its weights. Checked first so a fresh container has a
# working model immediately rather than after a 4.4GB download nobody was told
# about.
BUNDLED_DIR = Path(os.getenv("PAS_LLM_DIR", "/opt/models/llm"))


def model_path() -> Path:
    """Where the weights live.

    The bundled copy wins when it is present; otherwise the shared models volume,
    which is where a lean build downloads to on first use.
    """
    name = (settings.llm_model or DEFAULT_MODEL).strip()
    bundled = BUNDLED_DIR / name
    if bundled.exists():
        return bundled
    return settings.models_dir / "llm" / name


def _runtime_importable() -> bool:
    """Whether llama.cpp can actually be loaded here.

    Not just ImportError: the CUDA build raises *RuntimeError* at import when
    `libcuda.so.1` is absent, which is exactly what happens on a host with no
    NVIDIA driver. Catching only ImportError let that escape into the request
    that asked for clip suggestions.
    """
    try:
        import llama_cpp  # noqa: F401

        return True
    except Exception:
        return False


def available() -> bool:
    """Whether a scoring pass could run right now."""
    if not settings.llm_enabled:
        return False
    if not _runtime_importable():
        return False
    return model_path().exists()


def runtime_status() -> dict:
    """What the admin screen needs to explain the current state."""
    runtime = _runtime_importable()
    path = model_path()
    return {
        "enabled": settings.llm_enabled,
        "runtime_installed": runtime,
        "model": path.name,
        "model_present": path.exists(),
        "model_bytes": path.stat().st_size if path.exists() else 0,
        "gpu_layers": settings.llm_gpu_layers,
        "loaded": _model is not None,
        "ready": available(),
    }


def download(on_progress=None) -> Path:
    """Fetch the weights once, into the models volume.

    Written to a temporary name and moved into place, so an interrupted download
    cannot leave a half-file that looks like a valid model on the next start.
    """
    import urllib.request

    path = model_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = settings.llm_model_url or DEFAULT_MODEL_URL
    partial = path.with_suffix(path.suffix + ".part")

    log.info("Downloading language model from %s", url)
    with urllib.request.urlopen(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done / total)
    partial.replace(path)
    return path


def _gpu_layers() -> int:
    """How much of the model to put on the GPU.

    -1 offloads everything, which is what you want whenever a card is present:
    this model is under 5GB and both the cards here have far more. Falling back
    to 0 keeps it working on a machine with no GPU, just slowly.
    """
    configured = settings.llm_gpu_layers
    if configured is not None:
        return configured
    try:
        from app.services.gpu import discover_gpus

        return -1 if discover_gpus() else 0
    except Exception:
        return 0


def load():
    """Load and cache the model. Returns None when it cannot be used."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed or not settings.llm_enabled:
        return None

    with _lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from llama_cpp import Llama
        except Exception as error:
            # A CUDA build with no driver present raises RuntimeError here, not
            # ImportError.
            log.info("Language model runtime unusable (%s); scoring stays heuristic", error)
            _load_failed = True
            return None

        path = model_path()
        if not path.exists():
            log.info("Language model weights not present at %s", path)
            return None

        try:
            layers = _gpu_layers()
            _model = Llama(
                model_path=str(path),
                n_ctx=CONTEXT_TOKENS,
                n_gpu_layers=layers,
                # One device at a time: the other card is doing Whisper.
                main_gpu=int(settings.llm_gpu_index or 0),
                n_threads=max(2, (os.cpu_count() or 8) // 4),
                verbose=False,
            )
            log.info("Language model loaded from %s (gpu_layers=%s)", path.name, layers)
        except Exception as error:
            log.warning("Could not load the language model: %s", error)
            _load_failed = True
            return None
    return _model


def unload() -> None:
    """Release the model and its VRAM."""
    global _model, _load_failed
    with _lock:
        _model = None
        _load_failed = False


# The model rates and selects. It never writes.
#
# An earlier version asked it for a title, and it produced things like "Home
# Tour" and "IT to Care" — reasonable summaries, and words the speaker never
# said. A title is content: it goes on the post, and sometimes into the frame.
# Putting invented phrasing into a guest's mouth is not something a tool should
# do quietly, however good the phrasing is.
#
# So the model picks *which line* of the excerpt is the strongest opening, by
# number, and that line is used verbatim. Judgement is what it is good at;
# authorship is not its job here.
PROMPT = """You rate podcast excerpts for use as short social video clips.

Rate this excerpt from 0 to 10 on each of:
- hook: does the opening make someone stop scrolling?
- standalone: does it make sense with no other context?
- interest: is the idea surprising, useful, funny, or emotionally real?

Also say whether this excerpt is advertising (ad: 1) or normal conversation
(ad: 0). Advertising includes sponsor messages, promo codes, and any product,
brand or company being described or praised by the host — even without a
code or a link, and even if it sounds like a story.

Also choose which numbered line below is the strongest opening line, and give
one short reason for the rating (under 12 words).

Do NOT write a title. Do NOT rewrite, summarise or paraphrase the speaker.
Only choose a line number.

Reply with JSON only, in exactly this form:
{{"hook": 0, "standalone": 0, "interest": 0, "ad": 0, "best_line": 1, "reason": ""}}

Lines:
{lines}
"""

# What a host says when the sponsor is paying. Any two of these in one
# excerpt is an ad read; one alone can be conversation ("I got a discount").
AD_PENALTY = 0.15
# How close to a detected read a clip can be before it is treated as part of it.
AD_BLOCK_SECONDS = 60.0
_AD_PHRASES = (
    "promo code", "use code", "use the code", "discount code", "coupon",
    "brought to you by", "sponsored by", "this episode is sponsored",
    "today's sponsor", "our sponsor", "sponsor of", "% off", "percent off",
    "free shipping", "free trial", "sign up today", "go to www", "dot com slash",
    ".com/", "link in the description", "link in the show notes", "checkout",
    "check out", "subscribe today", "terms and conditions apply", "limited time",
)


def looks_like_ad(text: str) -> bool:
    """True when the words alone give the sponsor away."""
    lowered = (text or "").lower()
    hits = sum(1 for phrase in _AD_PHRASES if phrase in lowered)
    return hits >= 2 or "promo code" in lowered or "sponsored by" in lowered or "brought to you by" in lowered


# Sentence-ish splitting, only to number the lines the model chooses between.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _lines_of(text: str, limit: int = 12) -> list[str]:
    """Split an excerpt into numbered candidate opening lines."""
    parts = [part.strip() for part in _SENTENCE.split(text.strip()) if part.strip()]
    return parts[:limit] if parts else ([text.strip()] if text.strip() else [])


_JSON = re.compile(r"\{.*?\}", re.DOTALL)


def _parse(reply: str, lines: list[str]) -> dict | None:
    """Read the model's JSON, tolerating the prose it sometimes wraps it in.

    `lines` is the excerpt the model was shown. The chosen line is resolved back
    to that list, so the headline can only ever be something the speaker said —
    a line number the model invents resolves to nothing and is dropped.
    """
    match = _JSON.search(reply or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    try:
        rating = {
            "hook": max(0.0, min(10.0, float(data.get("hook", 0)))),
            "standalone": max(0.0, min(10.0, float(data.get("standalone", 0)))),
            "interest": max(0.0, min(10.0, float(data.get("interest", 0)))),
        }
    except (TypeError, ValueError):
        return None

    # The reason is the model's own assessment and is shown as such in the UI,
    # next to the heuristic tags. It is never used as content.
    rating["reason"] = str(data.get("reason", "")).strip()[:120]
    try:
        rating["ad"] = bool(int(data.get("ad", 0)))
    except (TypeError, ValueError):
        rating["ad"] = False

    rating["headline"] = ""
    try:
        chosen = int(data.get("best_line", 0))
    except (TypeError, ValueError):
        chosen = 0
    # One-based in the prompt, because models count from one more reliably.
    if 1 <= chosen <= len(lines):
        rating["headline"] = lines[chosen - 1]
    return rating


def rate(text: str) -> dict | None:
    """Rate one excerpt, or None if the model is unusable."""
    model = load()
    if model is None:
        return None

    lines = _lines_of(text[:4000])
    if not lines:
        return None
    numbered = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))

    try:
        response = model.create_chat_completion(
            messages=[{"role": "user", "content": PROMPT.format(lines=numbered)}],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.2,
            # Low variance matters more than variety: these are comparisons.
            top_p=0.9,
        )
        reply = response["choices"][0]["message"]["content"]
    except Exception as error:
        log.warning("Language model scoring failed: %s", error)
        return None
    return _parse(reply, lines)


# How much the model's judgement is allowed to move a candidate. The heuristics
# are reliable about form and the model is not always reliable about anything,
# so this re-orders a shortlist rather than replacing its ranking.
LLM_WEIGHT = 0.55


# Titles are shown in a card and can end up as post copy, so they are trimmed
# to a readable length — but only ever by cutting the speaker's own sentence
# short, never by rewording it.
TITLE_WORDS = 10


def _trim_title(line: str) -> str:
    words = line.split()
    trimmed = " ".join(words[:TITLE_WORDS]).rstrip(",;:").rstrip(".")
    if len(words) > TITLE_WORDS:
        trimmed += "…"
    return trimmed


def rerank(clips: list[dict], shortlist: int | None = None) -> list[dict]:
    """Re-order heuristic candidates by what they are actually about.

    Only the shortlist is read, because the heuristic pass exists precisely so
    the model never sees the whole episode. Anything it cannot rate keeps its
    heuristic position.
    """
    if not clips:
        return clips

    limit = shortlist or DEFAULT_SHORTLIST
    scored = list(clips)
    # The words first, so the model's ratings — the slow part — go to
    # candidates that could actually be clips rather than to promo codes.
    for clip in scored:
        if looks_like_ad(clip.get("text", "")):
            clip["score"] = round(clip.get("score", 0.0) * AD_PENALTY, 3)
            clip["ad"] = True
            clip.setdefault("reasons", []).insert(0, "Sounds like a sponsor read")
    candidates = [c for c in scored if not c.get("ad")]
    if not available():
        # No model: the heuristic order otherwise stands.
        candidates = []
    for clip in candidates[:limit]:
        rating = rate(clip.get("text", ""))
        if not rating:
            continue
        # 0..10 each, averaged to 0..1.
        judgement = (rating["hook"] + rating["standalone"] + rating["interest"]) / 30.0
        clip["llm"] = rating
        clip["score"] = round(clip.get("score", 0.0) + LLM_WEIGHT * judgement, 3)
        if rating["reason"]:
            clip.setdefault("reasons", []).insert(0, rating["reason"])
        if rating.get("ad") or looks_like_ad(clip.get("text", "")):
            # A sponsor read is the one thing nobody wants as their clip,
            # however well it "hooks". Pushed to the bottom, and labelled.
            clip["score"] = round(clip["score"] * AD_PENALTY, 3)
            clip["ad"] = True
            clip["reasons"] = ["Sounds like a sponsor read"] + [
                r for r in clip.get("reasons", []) if r != "Sounds like a sponsor read"
            ]

        # The headline is the line the model *chose*, checked back against the
        # excerpt before it is used. A title is content — it goes on the post —
        # so it may only ever be words the speaker actually said. Anything that
        # is not found verbatim in the clip is discarded and the heuristic title
        # stands.
        headline = (rating.get("headline") or "").strip()
        if headline and headline in clip.get("text", ""):
            clip["title"] = _trim_title(headline)

    # Sponsor reads come in blocks: the line before the promo code is the
    # product's origin story, and it carries none of the give-away words.
    # A clip that sits next to a detected read is part of the same block.
    flagged = [c for c in scored if c.get("ad")]
    for clip in scored:
        if clip.get("ad"):
            continue
        near = any(
            abs(float(clip.get("start", 0)) - float(ad.get("end", 0))) <= AD_BLOCK_SECONDS
            or abs(float(ad.get("start", 0)) - float(clip.get("end", 0))) <= AD_BLOCK_SECONDS
            or (float(clip.get("start", 0)) < float(ad.get("end", 0)) and float(clip.get("end", 0)) > float(ad.get("start", 0)))
            for ad in flagged
        )
        if near:
            clip["score"] = round(clip.get("score", 0.0) * AD_PENALTY, 3)
            clip["ad"] = True
            clip.setdefault("reasons", []).insert(0, "Right next to a sponsor read")
    scored.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return scored
