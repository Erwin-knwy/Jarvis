"""Offline fallback: handles a few common commands with simple rules when the
cloud brain is unreachable (no internet, API outage)."""
from __future__ import annotations

import re
from datetime import datetime

from .tools import macos, notes, schedule

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty five": 45, "sixty": 60, "a": 1, "an": 1}


def _n(word: str) -> float | None:
    word = word.strip().lower()
    try:
        return float(word)
    except ValueError:
        return _NUM.get(word)


def handle(text: str) -> str | None:
    t = text.lower().strip()

    if re.search(r"\bwhat(?:'s| is) the time\b|\bwhat time is it\b", t):
        return datetime.now().strftime("It's %H:%M.")
    if re.search(r"\bwhat(?:'s| is) (?:the )?(?:date|day)\b|\bwhat day is it\b", t):
        return datetime.now().strftime("It's %A, %d %B %Y.")

    m = re.search(r"(?:set )?(?:a )?timer for ([\w ]+?) (minute|minutes|min|second|seconds|sec)s?\b", t) or \
        re.search(r"([\w ]+?) (minute|minutes|second|seconds) timer", t)
    if m:
        n = _n(m.group(1).split()[-1] if m.group(1).split() else "")
        if n:
            kw = {"minutes": n} if m.group(2).startswith("min") else {"seconds": int(n)}
            return schedule.set_timer.call(kw)

    m = re.search(r"(?:set )?volume (?:to )?(\d{1,3})", t)
    if m and macos.IS_MAC:
        return macos.set_volume.call({"level": int(m.group(1))})
    if re.search(r"\bmute\b", t) and macos.IS_MAC:
        return macos.set_volume.call({"level": 0})

    m = re.search(r"(?:take a note|note|add a note|write down)[:,]?\s+(?:that\s+)?(.+)", t)
    if m:
        return notes.add_note.call({"text": m.group(1)})

    return None
