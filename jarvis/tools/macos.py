"""macOS control through AppleScript (osascript). Only specific, allowlisted actions."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime

from anthropic import beta_tool

RISK = {"quit_app": "confirm"}

IS_MAC = sys.platform == "darwin"


def osascript(script: str, timeout: float = 10) -> str:
    if not IS_MAC:
        raise RuntimeError("macOS only")
    p = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "osascript failed")
    return p.stdout.strip()


def _q(s: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _as_date(var: str, dt: datetime) -> str:
    """AppleScript lines that build `var` as a date without locale-dependent parsing."""
    return (
        f"set {var} to current date\n"
        f"set day of {var} to 1\n"
        f"set year of {var} to {dt.year}\n"
        f"set month of {var} to {dt.month}\n"
        f"set day of {var} to {dt.day}\n"
        f"set hours of {var} to {dt.hour}\n"
        f"set minutes of {var} to {dt.minute}\n"
        f"set seconds of {var} to 0\n"
    )


def _safe(fn):
    try:
        return fn()
    except subprocess.TimeoutExpired:
        return "Error: the app took too long to respond."
    except RuntimeError as e:
        msg = str(e)
        if "-1743" in msg or "Not authorized" in msg:
            return ("Error: macOS blocked automation. Allow it in System Preferences → "
                    "Security & Privacy → Privacy → Automation for your terminal.")
        return f"Error: {msg}"


_APP_RE = re.compile(r"^[\w .&'-]{1,60}$")


@beta_tool(eager_input_streaming=True)
def open_app(name: str) -> str:
    """Open (or bring to front) a Mac application, e.g. Safari, Music, Notes, Calendar.

    Args:
        name: Application name as shown in /Applications.
    """
    if not _APP_RE.match(name):
        return "Error: invalid app name."
    p = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    return f"Opened {name}." if p.returncode == 0 else f"Error: couldn't open {name}."


@beta_tool(eager_input_streaming=True)
def quit_app(name: str, confirmed: bool = False) -> str:
    """Quit a Mac application (unsaved work may prompt). Ask the user to confirm first.

    Args:
        name: Application name.
        confirmed: True only after the user explicitly confirmed.
    """
    if not _APP_RE.match(name):
        return "Error: invalid app name."
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user whether to quit {name}."
    return _safe(lambda: (osascript(f"tell application {_q(name)} to quit"), f"Quit {name}.")[1])


@beta_tool(eager_input_streaming=True)
def open_url(url: str) -> str:
    """Open a web page in the default browser on the Mac.

    Args:
        url: http(s) URL.
    """
    if not re.match(r"^https?://", url):
        return "Error: only http(s) URLs are allowed."
    subprocess.run(["open", url])
    return f"Opened {url}."


@beta_tool(eager_input_streaming=True)
def set_volume(level: int) -> str:
    """Set the Mac's output volume (0 = mute, 100 = max).

    Args:
        level: Volume 0-100.
    """
    level = max(0, min(100, int(level)))
    return _safe(lambda: (osascript(f"set volume output volume {level} without output muted"),
                          f"Volume set to {level}%.")[1])


@beta_tool(eager_input_streaming=True)
def media_control(action: str) -> str:
    """Control the Music app: play, pause, next, previous, or now_playing.

    Args:
        action: One of play, pause, next, previous, now_playing.
    """
    cmds = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
    if action == "now_playing":
        return _safe(lambda: osascript(
            'tell application "Music" to if player state is playing then '
            'return (name of current track) & " by " & (artist of current track) '
            'else return "Nothing is playing."'))
    if action not in cmds:
        return "Error: action must be play, pause, next, previous or now_playing."
    return _safe(lambda: (osascript(f'tell application "Music" to {cmds[action]}'), f"Music: {action}.")[1])


@beta_tool(eager_input_streaming=True)
def create_calendar_event(title: str, start: str, duration_minutes: int = 60, calendar: str = "") -> str:
    """Add an event to the Mac Calendar app (syncs to the user's iCloud/Google calendars).

    Args:
        title: Event title.
        start: Local start time, ISO format e.g. 2026-11-06T15:00:00.
        duration_minutes: Length in minutes.
        calendar: Calendar name; empty = first writable calendar.
    """
    try:
        dt = datetime.fromisoformat(start)
    except ValueError:
        return "Error: start must be ISO format."
    cal = f"calendar {_q(calendar)}" if calendar else "first calendar whose writable is true"
    script = (
        _as_date("d", dt)
        + 'tell application "Calendar"\n'
        + f"  tell {cal}\n"
        + f"    make new event with properties {{summary:{_q(title)}, start date:d, "
        + f"end date:(d + {int(duration_minutes)} * minutes)}}\n"
        + "  end tell\nend tell\n"
    )
    return _safe(lambda: (osascript(script, timeout=20), f"Added '{title}' on {dt:%a %d %b at %H:%M}.")[1])


@beta_tool(eager_input_streaming=True)
def list_calendar_events(day: str = "") -> str:
    """List Calendar events for a day (default today).

    Args:
        day: Date in ISO format, e.g. 2026-11-06. Empty = today.
    """
    try:
        dt = datetime.fromisoformat(day) if day else datetime.now()
    except ValueError:
        return "Error: day must be ISO format."
    dt = dt.replace(hour=0, minute=0)
    script = (
        _as_date("d0", dt)
        + "set d1 to d0 + 1 * days\n"
        + 'set out to ""\n'
        + 'tell application "Calendar"\n'
        + "  repeat with c in calendars\n"
        + "    repeat with e in (every event of c whose start date ≥ d0 and start date < d1)\n"
        + "      set t to start date of e\n"
        + "      set out to out & (time string of t) & \" — \" & (summary of e) & linefeed\n"
        + "    end repeat\n  end repeat\nend tell\nreturn out\n"
    )
    res = _safe(lambda: osascript(script, timeout=30))
    return res or f"No events on {dt:%A %d %B}."


@beta_tool(eager_input_streaming=True)
def add_apple_reminder(title: str, due: str = "") -> str:
    """Add an item to the Apple Reminders app (syncs to iPhone/iCloud). For Jarvis's own
    spoken reminders use set_reminder instead.

    Args:
        title: Reminder text.
        due: Optional local due time in ISO format.
    """
    props = f"name:{_q(title)}"
    pre = ""
    if due:
        try:
            pre = _as_date("d", datetime.fromisoformat(due))
            props += ", due date:d"
        except ValueError:
            return "Error: due must be ISO format."
    script = pre + f'tell application "Reminders" to make new reminder with properties {{{props}}}'
    return _safe(lambda: (osascript(script, timeout=20), f"Added '{title}' to Reminders.")[1])


@beta_tool(eager_input_streaming=True)
def system_status() -> str:
    """Mac battery level, charging state, Wi-Fi network, disk space and uptime."""
    parts = []
    try:
        b = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
        m = re.search(r"(\d+)%;\s*([\w ]+);", b)
        if m:
            parts.append(f"Battery {m.group(1)}% ({m.group(2).strip()})")
        wifi = subprocess.run(
            ["networksetup", "-getairportnetwork", "en0"], capture_output=True, text=True
        ).stdout.strip()
        if wifi:
            parts.append(wifi.replace("Current Wi-Fi Network: ", "Wi-Fi: "))
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()[-1].split()
        parts.append(f"Disk: {df[3]} free of {df[1]}")
        up = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
        parts.append("Uptime: " + up.split(",")[0].split("up", 1)[-1].strip())
    except Exception as e:  # status is best-effort
        parts.append(f"(partial: {e})")
    return "; ".join(parts)


@beta_tool(eager_input_streaming=True)
def lock_screen() -> str:
    """Lock the Mac screen (start the screensaver/login window)."""
    subprocess.run(["pmset", "displaysleepnow"])
    return "Screen locked."


TOOLS = [open_app, quit_app, open_url, set_volume, media_control, create_calendar_event,
         list_calendar_events, add_apple_reminder, system_status, lock_screen] if IS_MAC else []
