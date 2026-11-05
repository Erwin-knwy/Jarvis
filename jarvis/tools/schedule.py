"""Timers and reminders (stored in SQLite, fired by jarvis.scheduler)."""
from __future__ import annotations

from datetime import datetime, timedelta

from anthropic import beta_tool

from .. import db

RISK = {"cancel_reminder": "safe"}


def _fmt(dt: datetime) -> str:
    return dt.strftime("%a %d %b %H:%M:%S")


@beta_tool(eager_input_streaming=True)
def set_timer(minutes: float = 0, seconds: int = 0, label: str = "timer") -> str:
    """Start a countdown timer that announces itself when it ends (e.g. "5 minute tea timer").

    Args:
        minutes: Minutes on the timer.
        seconds: Extra seconds on the timer.
        label: Short name for the timer, e.g. "tea".
    """
    total = minutes * 60 + seconds
    if total <= 0:
        return "Error: timer length must be positive."
    due = datetime.now() + timedelta(seconds=total)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO reminders(text, due_at, kind) VALUES (?,?, 'timer')",
            (label, due.isoformat(timespec="seconds")),
        )
    return f"Timer #{cur.lastrowid} '{label}' set, ends at {_fmt(due)}."


@beta_tool(eager_input_streaming=True)
def set_reminder(text: str, when: str) -> str:
    """Remind the user about something at a specific date/time. Jarvis will say it
    out loud and push a notification to the phone. Use for "remind me ..." requests.

    Args:
        text: What to remind the user about.
        when: Local date-time in ISO format, e.g. 2026-11-06T09:00:00. Compute it from the current time given in the message.
    """
    try:
        due = datetime.fromisoformat(when)
    except ValueError:
        return "Error: 'when' must be ISO format like 2026-11-06T09:00:00."
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    if due < datetime.now() - timedelta(minutes=1):
        return f"Error: {when} is in the past."
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO reminders(text, due_at, kind) VALUES (?,?, 'reminder')",
            (text, due.isoformat(timespec="seconds")),
        )
    return f"Reminder #{cur.lastrowid} set for {_fmt(due)}: {text}"


@beta_tool(eager_input_streaming=True)
def list_reminders() -> str:
    """List pending timers and reminders with their IDs and remaining time."""
    rows = db.query("SELECT * FROM reminders WHERE done=0 ORDER BY due_at")
    if not rows:
        return "No pending timers or reminders."
    now = datetime.now()
    out = []
    for r in rows:
        due = datetime.fromisoformat(r["due_at"])
        left = due - now
        mins = max(0, int(left.total_seconds() // 60))
        out.append(f"#{r['id']} {r['kind']} '{r['text']}' at {_fmt(due)} ({mins} min left)")
    return "\n".join(out)


@beta_tool(eager_input_streaming=True)
def cancel_reminder(reminder_id: int) -> str:
    """Cancel a pending timer or reminder by ID (get IDs from list_reminders).

    Args:
        reminder_id: The ID of the timer/reminder.
    """
    with db.tx() as c:
        n = c.execute("UPDATE reminders SET done=1 WHERE id=? AND done=0", (reminder_id,)).rowcount
    return f"Cancelled #{reminder_id}." if n else f"No pending timer/reminder #{reminder_id}."


TOOLS = [set_timer, set_reminder, list_reminders, cancel_reminder]
