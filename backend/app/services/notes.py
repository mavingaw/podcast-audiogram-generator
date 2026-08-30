"""Show notes, titles and keywords, written from the transcript.

The same local model that rates clips reads the whole episode and produces
what a podcaster types out by hand every week: title ideas, a description
for the feed, the highlights, and keywords/hashtags for the socials.

A full episode is longer than the model's window, so it is read the way a
person skims: each stretch of the transcript is boiled down to notes, and
the notes are written up into the final piece. Generation runs on a
background thread with its progress in the settings table — no new job
kind, nothing that can jam the render lanes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from app.db.models import AppSetting, MediaAsset

log = logging.getLogger(__name__)

CHUNK_WORDS = 1300
MAX_CHUNKS = 14  # ~90 minutes of talk; beyond that the map pass is capped.
STALE_SECONDS = 15 * 60

CHUNK_PROMPT = """These are lines {start} to {end} of a podcast transcript.

Write 3 to 5 short factual notes about what is discussed: topics, stories,
strong opinions, guests, anything a listener would remember. One line each,
no numbering, no commentary about the transcript itself. Skip sponsor reads
and advertising entirely.

Transcript:
{text}
"""

FINAL_PROMPT = """You are writing show notes for a podcast episode, from these
notes taken while listening:

{notes}

Reply with JSON only, exactly this shape:
{{"titles": ["three short episode title ideas"],
 "description": "one paragraph, 2-4 sentences, written to make someone press play; no hashtags",
 "highlights": ["4 to 6 bullet points of what is covered, short"],
 "keywords": ["8 to 12 lowercase search keywords"],
 "hashtags": ["6 to 10 hashtags starting with #"]}}

Do not invent guests, names or claims that are not in the notes. Skip
anything that reads like advertising.
"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)
_threads: dict[str, threading.Thread] = {}
_spawn_lock = threading.Lock()


class NotesError(RuntimeError):
    pass


def _key(media_id: str) -> str:
    return f"notes:{media_id}"


def status(db, media_id: str) -> dict:
    row = db.get(AppSetting, _key(media_id))
    if not row or not row.value:
        return {"status": "none"}
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return {"status": "none"}


def _write(db, media_id: str, payload: dict) -> None:
    row = db.get(AppSetting, _key(media_id)) or AppSetting(key=_key(media_id), value="")
    row.value = json.dumps(payload)
    db.merge(row)
    db.commit()


def start(db, media_id: str, owner_id: str) -> dict:
    """Kick generation off in the background; answer the current state."""
    from app.services import llm

    if not llm.available():
        raise NotesError("The language model is not available on this server.")
    current = status(db, media_id)
    if current.get("status") == "working" and time.time() - float(current.get("started", 0)) < STALE_SECONDS:
        return current
    fresh = {"status": "working", "started": time.time()}
    _write(db, media_id, fresh)
    with _spawn_lock:
        thread = _threads.get(media_id)
        if thread is None or not thread.is_alive():
            thread = threading.Thread(
                target=_run, args=(media_id, owner_id), name=f"pas-notes-{media_id[:8]}", daemon=True
            )
            _threads[media_id] = thread
            thread.start()
    return fresh


def _run(media_id: str, owner_id: str) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            media = db.get(MediaAsset, media_id)
            if media is None or media.owner_id != owner_id or not media.transcript_json:
                raise NotesError("No transcript to read")
            transcript = json.loads(media.transcript_json)
            result = generate(transcript, title=media.original_name)
            _write(db, media_id, {"status": "done", "finished": time.time(), "result": result})
        except Exception as error:
            log.warning("show notes failed for %s", media_id, exc_info=True)
            _write(db, media_id, {"status": "failed", "error": str(error)[:300]})


def generate(transcript: dict, title: str = "") -> dict:
    """The whole pipeline, synchronous — the thread and the tests call this."""
    from app.services import llm

    chunks = _chunks(transcript)
    if not chunks:
        raise NotesError("The transcript is empty")
    notes: list[str] = []
    for index, chunk in enumerate(chunks[:MAX_CHUNKS], start=1):
        reply = llm.complete(
            CHUNK_PROMPT.format(start=index, end=len(chunks), text=chunk), max_tokens=300
        )
        for line in (reply or "").splitlines():
            line = line.strip().lstrip("-•* ").strip()
            if len(line) > 10:
                notes.append(line)
    if not notes:
        raise NotesError("The model produced nothing to work with")
    reply = llm.complete(FINAL_PROMPT.format(notes="\n".join(f"- {n}" for n in notes[:80])), max_tokens=900)
    parsed = _parse(reply or "")
    if parsed is None:
        raise NotesError("The model's write-up could not be read")
    parsed["notes_taken"] = len(notes)
    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
    return parsed


def _chunks(transcript: dict) -> list[str]:
    words: list[str] = []
    for segment in transcript.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if text:
            words.extend(text.split())
    return [
        " ".join(words[i: i + CHUNK_WORDS])
        for i in range(0, len(words), CHUNK_WORDS)
    ]


def _parse(reply: str) -> dict | None:
    match = _JSON.search(reply)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    out = {
        "titles": [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:3],
        "description": str(data.get("description", "")).strip(),
        "highlights": [str(h).strip() for h in (data.get("highlights") or []) if str(h).strip()][:6],
        "keywords": [str(k).strip().lower() for k in (data.get("keywords") or []) if str(k).strip()][:12],
        "hashtags": [
            (h if str(h).startswith("#") else "#" + str(h).lstrip("#")).strip()
            for h in (data.get("hashtags") or [])
            if str(h).strip()
        ][:10],
    }
    if not out["description"] and not out["highlights"]:
        return None
    return out
