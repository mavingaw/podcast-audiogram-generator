"""Slowing down password guessing.

The moment this is reachable from the internet, the sign-in form is the whole
security boundary, and an unthrottled one can be guessed at as fast as the
network allows. Nothing here is a substitute for a decent password; it turns an
online guessing attack from thousands of attempts a minute into a handful.

Deliberately in-process and in-memory. A shared store would be the right answer
for several API instances, but this runs as one container, and adding Redis to
an appliance to hold a counter that resets on restart is a worse trade than the
counter resetting on restart.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# Failures allowed before a wait is imposed. Generous enough that somebody
# genuinely misremembering their password is never locked out.
FREE_ATTEMPTS = 5

# How long the penalty lasts once it starts, doubling per failure to a ceiling.
BASE_DELAY = 2.0
MAX_DELAY = 300.0

# Failures older than this are forgotten, so one bad afternoon does not follow
# somebody around.
WINDOW = 900.0


@dataclass
class _Record:
    failures: int = 0
    blocked_until: float = 0.0
    last_seen: float = field(default_factory=time.time)


_lock = threading.Lock()
_records: dict[str, _Record] = {}


def _prune(now: float) -> None:
    stale = [key for key, record in _records.items() if now - record.last_seen > WINDOW]
    for key in stale:
        _records.pop(key, None)


def retry_after(key: str) -> float:
    """Seconds the caller must wait, or 0 if they may try now."""
    now = time.time()
    with _lock:
        _prune(now)
        record = _records.get(key)
        if record is None:
            return 0.0
        return max(0.0, record.blocked_until - now)


def record_failure(key: str) -> float:
    """Note a failed attempt. Returns how long the caller must now wait."""
    now = time.time()
    with _lock:
        _prune(now)
        record = _records.setdefault(key, _Record())
        record.last_seen = now
        record.failures += 1
        if record.failures <= FREE_ATTEMPTS:
            return 0.0
        # Doubling from the first penalised attempt: 2s, 4s, 8s, and so on.
        delay = min(MAX_DELAY, BASE_DELAY * (2 ** (record.failures - FREE_ATTEMPTS - 1)))
        record.blocked_until = now + delay
        return delay


def record_success(key: str) -> None:
    """Forget a caller's failures once they prove who they are."""
    with _lock:
        _records.pop(key, None)


def reset() -> None:
    """Clear everything. For tests."""
    with _lock:
        _records.clear()


def key_for(request, username: str) -> str:
    """What counts as one caller.

    Both the address and the username, so guessing many passwords for one
    account is throttled even from a rotating address, and hammering many
    accounts from one address is throttled too. Behind Cloudflare the useful
    address is the forwarded one; the socket address would be the tunnel.
    """
    forwarded = ""
    try:
        forwarded = (request.headers.get("cf-connecting-ip")
                     or request.headers.get("x-forwarded-for", "").split(",")[0]
                     or "").strip()
    except Exception:
        forwarded = ""
    client = forwarded or (getattr(request.client, "host", "") if request.client else "")
    return f"{client}|{(username or '').strip().lower()}"
