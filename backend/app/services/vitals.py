"""Notice when the server stops being able to answer.

Written after an incident that took an hour to understand from the outside.
Every job had finished, the logs showed nothing but `200 OK`, and the
application was to all appearances healthy — while `GET /api/media` took over
five minutes and the container burned five and a half cores doing nothing. From
the browser it looked like buttons had stopped working, because a click that
posts a request and waits is indistinguishable from a click that was ignored.

Restarting fixed it, which is the worst kind of fix: it destroys the evidence.

Two things are recorded here so that the next occurrence explains itself
instead of having to be caught live:

  - how long each request took, logged when it is slow enough to be felt
  - process CPU, memory and thread count, sampled while it drifts

Neither costs anything measurable. `time.perf_counter()` around a request is
tens of nanoseconds, and the sampler wakes once a minute.

No psutil: it is not a dependency of this project, and everything needed is in
/proc, which is where psutil reads it from anyway. On a platform without /proc
the sampler reports what it can and stays quiet about the rest.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("kinder.vitals")

# Slower than a person will sit through without deciding something is broken.
SLOW_REQUEST_SECONDS = float(os.getenv("PAS_SLOW_REQUEST_SECONDS", "2.0"))

# Long enough to be worth interrupting the log for, whatever else is happening.
STUCK_REQUEST_SECONDS = float(os.getenv("PAS_STUCK_REQUEST_SECONDS", "30.0"))

SAMPLE_SECONDS = float(os.getenv("PAS_VITALS_SECONDS", "60"))

# Polling endpoints are the majority of traffic and the least interesting; they
# are still logged when slow, because a slow poll is exactly the symptom.
_QUIET_PATHS = ("/api/health",)


def _self_stat() -> dict:
    """CPU seconds, resident memory and thread count for this process."""
    stat: dict = {}
    try:
        fields = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()
        ticks = os.sysconf("SC_CLK_TCK")
        # Fields after the comm field: utime is 11, stime 12, num_threads 17
        # (0-indexed from "state"), per proc(5).
        stat["cpu_seconds"] = (int(fields[11]) + int(fields[12])) / ticks
        stat["threads"] = int(fields[17])
    except (OSError, IndexError, ValueError):
        pass
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                stat["rss_mb"] = int(line.split()[1]) / 1024
                break
    except (OSError, IndexError, ValueError):
        pass
    return stat


def _sampler() -> None:
    """Log CPU, memory and threads whenever they move meaningfully.

    Logging every sample would bury the interesting one in a day of identical
    lines, so a sample is only written when something has actually changed:
    sustained CPU, memory that has grown, or threads that have appeared. The
    line before a wedge is the one worth having.
    """
    previous = _self_stat()
    previous_at = time.monotonic()
    reported_busy = False

    while True:
        time.sleep(SAMPLE_SECONDS)
        try:
            current = _self_stat()
            now = time.monotonic()
            elapsed = max(0.001, now - previous_at)

            cores = 0.0
            if "cpu_seconds" in current and "cpu_seconds" in previous:
                cores = (current["cpu_seconds"] - previous["cpu_seconds"]) / elapsed

            threads = current.get("threads", 0)
            rss = current.get("rss_mb", 0.0)
            grew = rss - previous.get("rss_mb", rss)

            # Busy with no work to do is the signature of the incident this
            # module exists for: a thread pool left spinning after its job
            # finished, starving the event loop that answers requests.
            busy = cores >= 1.0
            if busy and not _work_in_progress():
                if not reported_busy:
                    logger.warning(
                        "vitals: %.1f cores busy with no running job "
                        "(threads=%d rss=%.0fMB) — if requests are slow, this "
                        "is why; a runtime has left its thread pool spinning",
                        cores, threads, rss,
                    )
                    reported_busy = True
            else:
                reported_busy = False

            if busy or abs(grew) > 256:
                logger.info(
                    "vitals: cores=%.2f threads=%d rss=%.0fMB (%+.0fMB)",
                    cores, threads, rss, grew,
                )

            previous, previous_at = current, now
        except Exception:
            # A monitor must never be the thing that takes the server down.
            continue


def _work_in_progress() -> bool:
    """Whether any job is actually running, for the 'busy while idle' check."""
    try:
        from sqlalchemy import select

        from app.db.models import Job, JobStatus
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            return db.scalar(
                select(Job.id).where(Job.status == JobStatus.running).limit(1)
            ) is not None
    except Exception:
        # Unknown is treated as busy, so an unreachable database does not
        # produce a warning every minute about a spin that may not exist.
        return True


def start() -> None:
    if SAMPLE_SECONDS <= 0:
        return
    threading.Thread(target=_sampler, name="vitals", daemon=True).start()


async def timing_middleware(request, call_next):
    """Log requests that took long enough for somebody to notice.

    The incident produced no error and no warning anywhere: the requests all
    returned 200, just minutes later than they should have. A duration is the
    only signal that distinguishes a healthy server from that one.
    """
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.warning(
            "%s %s failed after %.1fs",
            request.method, request.url.path, time.perf_counter() - started,
        )
        raise
    took = time.perf_counter() - started

    if took >= STUCK_REQUEST_SECONDS:
        logger.error(
            "%s %s took %.1fs — the server is not keeping up",
            request.method, request.url.path, took,
        )
    elif took >= SLOW_REQUEST_SECONDS and request.url.path not in _QUIET_PATHS:
        logger.warning(
            "%s %s took %.1fs", request.method, request.url.path, took
        )
    return response
