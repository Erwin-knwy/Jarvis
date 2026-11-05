"""Tiny in-process pub/sub so background jobs (timers, reminders, briefing) can
announce things through whatever front-ends are running (voice, web, phone)."""
from __future__ import annotations

import logging
import threading
from typing import Callable

import httpx

from . import config

log = logging.getLogger(__name__)
_listeners: list[Callable[[str], None]] = []
_lock = threading.Lock()


def subscribe(fn: Callable[[str], None]) -> Callable[[], None]:
    with _lock:
        _listeners.append(fn)

    def unsubscribe():
        with _lock:
            if fn in _listeners:
                _listeners.remove(fn)

    return unsubscribe


def announce(text: str, push: bool = True, title: str = "Jarvis") -> None:
    """Speak/show `text` on every active front-end and push it to the phone."""
    with _lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(text)
        except Exception:  # a broken listener must not stop the others
            log.exception("announce listener failed")
    if push:
        push_to_phone(text, title=title)


def push_to_phone(text: str, title: str = "Jarvis") -> bool:
    n = config.get()["notifications"]
    topic = n.get("ntfy_topic")
    if not topic:
        return False
    try:
        r = httpx.post(
            f"{n['ntfy_server'].rstrip('/')}/{topic}",
            content=text.encode(),
            headers={"Title": title, "Tags": "robot"},
            timeout=5,
        )
        return r.status_code < 300
    except httpx.HTTPError:
        log.warning("ntfy push failed", exc_info=True)
        return False
