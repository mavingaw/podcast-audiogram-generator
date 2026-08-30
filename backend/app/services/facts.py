"""Something to read while the machine works.

A transcription of an hour-long episode takes minutes, and a bar creeping
across the screen is only reassuring for the first thirty seconds. A line of
trivia under it gives the eye somewhere to go, and the next one arriving is
its own sign that the page is alive.

Facts come from a public random-facts service, fetched in the background and
kept in a pool so the browser never waits on the internet. If the service is
unreachable — a box with no outbound access, a rate limit, an outage — the
bundled lines below are used instead. Nothing here is ever a reason for a job
to fail, and no request is made on the user's behalf: the box fetches, the
browser asks the box.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

SOURCE = "https://uselessfacts.jsph.pl/api/v2/facts/random?language=en"
POOL_TARGET = 60
REFRESH_SECONDS = 30 * 60
FETCH_TIMEOUT = 4.0

# Written here, not scraped. Light, harmless, and true.
BUNDLED: tuple[str, ...] = (
    "Honey never spoils. Edible honey has been found in Egyptian tombs.",
    "A group of flamingos is called a flamboyance.",
    "Octopuses have three hearts and blue blood.",
    "The word “podcast” was coined in 2004, blending “iPod” and “broadcast”.",
    "Sound travels about four times faster in water than in air.",
    "Your ears never stop hearing — your brain just ignores sounds while you sleep.",
    "The first radio broadcast of a human voice was in 1906. It included a violin solo.",
    "Bananas are berries. Strawberries are not.",
    "A day on Venus is longer than a year on Venus.",
    "Cows have best friends and get stressed when separated from them.",
    "The dot over a lowercase i or j is called a tittle.",
    "There are more possible games of chess than atoms in the observable universe.",
    "Sloths can hold their breath longer than dolphins can.",
    "The shortest war in history lasted about 38 minutes.",
    "Wombats produce cube-shaped droppings.",
    "A jiffy is an actual unit of time: one hundredth of a second.",
    "Otters hold hands while they sleep so they do not drift apart.",
    "The Eiffel Tower grows about 15 centimetres taller in summer heat.",
    "Humans share about 60% of their DNA with bananas.",
    "The average person speaks around 16,000 words a day.",
    "Butterflies taste with their feet.",
    "Scotland's national animal is the unicorn.",
    "The first computer mouse was made of wood.",
    "A single strand of spaghetti is called a spaghetto.",
    "There is a species of jellyfish that can revert to its juvenile form.",
    "Most microphones hear better than you do: they pick up the fridge, the traffic and the neighbour's dog.",
    "Radio waves from the earliest broadcasts are now roughly 100 light-years out into space.",
    "The hashtag symbol is properly called an octothorpe.",
    "Koalas have fingerprints almost indistinguishable from human ones.",
    "Venus is the only planet that spins clockwise.",
    "Some cats are allergic to humans.",
    "It takes about 8 minutes for sunlight to reach Earth.",
    "The inventor of the Pringles can is buried in one.",
    "Sea otters have a favourite rock they keep in a pouch under their arm.",
    "Cheetahs cannot roar; they chirp, purr and meow.",
    "An adult human is made of roughly seven octillion atoms.",
)


class FactPool:
    def __init__(self) -> None:
        self._facts: list[str] = []
        self._lock = threading.Lock()
        self._last = 0.0
        self._filling = False

    def sample(self, count: int) -> list[str]:
        """`count` facts, freshest pool first, bundled lines behind."""
        self._maybe_refresh()
        with self._lock:
            pool = list(self._facts)
        random.shuffle(pool)
        picked = pool[:count]
        if len(picked) < count:
            spare = [f for f in BUNDLED if f not in picked]
            random.shuffle(spare)
            picked.extend(spare[: count - len(picked)])
        return picked

    def _maybe_refresh(self) -> None:
        with self._lock:
            due = time.monotonic() - self._last > REFRESH_SECONDS or len(self._facts) < POOL_TARGET // 2
            if not due or self._filling:
                return
            self._filling = True
        threading.Thread(target=self._fill, name="pas-facts", daemon=True).start()

    def _fill(self) -> None:
        fresh: list[str] = []
        try:
            for _ in range(POOL_TARGET):
                fact = fetch_one()
                if not fact:
                    break
                if fact not in fresh:
                    fresh.append(fact)
        except Exception:  # pragma: no cover - a failed refresh is not an event
            log.debug("fact refresh stopped early", exc_info=True)
        finally:
            with self._lock:
                if fresh:
                    self._facts = fresh
                self._last = time.monotonic()
                self._filling = False


def fetch_one() -> str | None:
    """One fact from the public service, or None when it cannot be had."""
    try:
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "Kinder/1.0 (fact pool)"})
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    text = str(payload.get("text", "")).strip()
    # Guard against anything odd coming back: too long, too short, or markup.
    if not 12 <= len(text) <= 220 or "<" in text:
        return None
    return text.replace("`", "'")


pool = FactPool()
