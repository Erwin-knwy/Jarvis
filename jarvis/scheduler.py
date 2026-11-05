"""Background jobs: fires due timers/reminders and the daily morning briefing."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from . import bus, config, db

log = logging.getLogger(__name__)


class Scheduler(threading.Thread):
    def __init__(self, brain_factory=None):
        super().__init__(daemon=True, name="jarvis-scheduler")
        self._halt = threading.Event()
        self._brain_factory = brain_factory
        self._last_briefing_day: str | None = None

    def stop(self):
        self._halt.set()

    def run(self):
        log.info("scheduler started")
        while not self._halt.wait(5):
            try:
                self._fire_due()
                self._maybe_briefing()
            except Exception:
                log.exception("scheduler tick failed")

    def _fire_due(self):
        now = datetime.now().isoformat(timespec="seconds")
        rows = db.query("SELECT * FROM reminders WHERE done=0 AND due_at <= ?", (now,))
        for r in rows:
            with db.tx() as c:
                c.execute("UPDATE reminders SET done=1 WHERE id=?", (r["id"],))
            if r["kind"] == "timer":
                msg = f"Your {r['text']} timer is done." if r["text"] != "timer" else "Your timer is done."
                bus.announce(msg, title="⏰ Timer")
            else:
                bus.announce(f"Reminder: {r['text']}", title="🔔 Reminder")

    def _maybe_briefing(self):
        b = config.get()["briefing"]
        if not b.get("enabled") or self._brain_factory is None:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.strftime("%H:%M") != b.get("time", "08:00") or self._last_briefing_day == today:
            return
        self._last_briefing_day = today
        threading.Thread(target=self._briefing, daemon=True).start()

    def _briefing(self):
        brain = self._brain_factory()
        text = brain.ask(
            "Give me my morning briefing: greet me, today's weather for my home city, "
            "my calendar events today, pending reminders, and two or three top news headlines. "
            "Keep it under 120 words, spoken style.",
            session="briefing",
            channel="voice",
        )
        brain.reset("briefing")
        bus.announce(text, title="☀️ Morning briefing")
