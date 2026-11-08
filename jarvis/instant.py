"""Instant commands: Siri-style phrases handled locally, with no AI and no network.

They run before the brain sees the request, so "Wi-Fi off", "volume 30" or "set a
timer for 5 minutes" happen immediately (Claude Code takes a couple of seconds), and
they keep working offline. Patterns are anchored to the whole utterance so longer,
nuanced requests still go to Claude.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from .tools import macos, notes, schedule, weather
from .tools import system as sysctl

NUMBERS = {
    "zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50,
    "sixty": 60, "seventy": 70, "seventy five": 75, "eighty": 80, "ninety": 90, "hundred": 100,
    "a hundred": 100, "one hundred": 100, "half an": 0.5, "half a": 0.5,
}
NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted((re.escape(k) for k in NUMBERS), key=len, reverse=True)) + r")"

FILLER = re.compile(
    r"^(?:(?:hey |ok |okay )?jarvis[,.!]?\s*)?(?:please\s+|can you\s+|could you\s+|would you\s+|will you\s+|"
    r"i want you to\s+|i'd like you to\s+|go ahead and\s+)*", re.I)


def num(s: str) -> float:
    s = s.strip().lower()
    try:
        return float(s)
    except ValueError:
        return float(NUMBERS.get(s, 0))


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = t.replace("wi-fi", "wifi").replace("wi fi", "wifi").replace("blue tooth", "bluetooth")
    t = re.sub(r"[“”\"!?]+", "", t)
    t = re.sub(r"[.,]+$", "", t).strip()
    t = FILLER.sub("", t).strip()
    t = re.sub(r"\s+(please|thanks|thank you)$", "", t)
    return re.sub(r"\s+", " ", t)


# (pattern, handler) — handler gets the match and returns the reply
Handler = Callable[[re.Match], str]
RULES: list[tuple[re.Pattern, Handler]] = []


def rule(pattern: str):
    def deco(fn: Handler):
        RULES.append((re.compile(r"^(?:" + pattern + r")$", re.I), fn))
        return fn
    return deco


# ---------------------------------------------------------------- time
@rule(r"what(?:'s| is) the time|what time is it(?: now)?|time|the time")
def _time(m):
    return datetime.now().strftime("It's %-I:%M %p.").replace("AM", "a.m.").replace("PM", "p.m.")


@rule(r"what(?:'s| is) (?:the |today's )?date(?: today)?|what day is (?:it|today)|what(?:'s| is) today")
def _date(m):
    return datetime.now().strftime("It's %A, %-d %B %Y.")


# ---------------------------------------------------------------- timers
@rule(r"(?:set |start )?(?:a |an )?timer (?:for )?" + NUM + r" ?(seconds?|secs?|minutes?|mins?|hours?|hrs?)"
      r"|" + NUM + r" ?(seconds?|secs?|minutes?|mins?|hours?) timer")
def _timer(m):
    n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    v = num(n)
    secs = v * (3600 if unit.startswith(("h",)) else 60 if unit.startswith("m") else 1)
    return schedule.set_timer.call({"seconds": int(secs)})


@rule(r"(?:what are my|list(?: my)?|show(?: my)?|any) (?:timers|reminders)|how long (?:is )?left(?: on (?:the|my) timer)?")
def _timers(m):
    return schedule.list_reminders.call({})


@rule(r"(?:cancel|stop|delete) (?:the |my )?(?:timer|alarm)")
def _cancel_timer(m):
    from . import db
    rows = db.query("SELECT id FROM reminders WHERE done=0 AND kind='timer' ORDER BY due_at")
    if not rows:
        return "There's no timer running."
    return schedule.cancel_reminder.call({"reminder_id": rows[0]["id"]})


# ---------------------------------------------------------------- sound
@rule(r"(?:set (?:the )?)?volume (?:to |at )?" + NUM + r"(?: ?%| percent)?|(?:turn|set) (?:the )?volume (?:to )?" + NUM + r"(?: ?%| percent)?")
def _vol_set(m):
    v = m.group(1) or m.group(2)
    return macos.set_volume.call({"level": int(num(v))})


@rule(r"(?:turn (?:it |the volume |the sound )?up|volume up|louder|turn up the (?:volume|sound)|increase (?:the )?volume)(?: a bit| a little)?")
def _vol_up(m):
    return sysctl.volume.call({"action": "up"})


@rule(r"(?:turn (?:it |the volume |the sound )?down|volume down|quieter|softer|turn down the (?:volume|sound)|lower (?:the )?volume|decrease (?:the )?volume)(?: a bit| a little)?")
def _vol_down(m):
    return sysctl.volume.call({"action": "down"})


@rule(r"mute|mute (?:the )?(?:sound|volume|mac|computer)|silence")
def _mute(m):
    return sysctl.volume.call({"action": "mute"})


@rule(r"unmute|unmute (?:the )?(?:sound|volume|mac|computer)")
def _unmute(m):
    return sysctl.volume.call({"action": "unmute"})


# ---------------------------------------------------------------- radios
@rule(r"(?:turn|switch) (on|off) (?:the )?(wifi|bluetooth)|(?:turn|switch) (?:the )?(wifi|bluetooth) (on|off)|(wifi|bluetooth) (on|off)"
      r"|(enable|disable) (?:the )?(wifi|bluetooth)")
def _radio(m):
    g = [x for x in m.groups() if x]
    state = next(x for x in g if x in ("on", "off", "enable", "disable"))
    dev = next(x for x in g if x in ("wifi", "bluetooth"))
    action = {"enable": "on", "disable": "off"}.get(state, state)
    return (sysctl.wifi if dev == "wifi" else sysctl.bluetooth).call({"action": action})


@rule(r"(?:is (?:the )?)?(wifi|bluetooth)(?: status| on)?|(?:what(?:'s| is) )?(?:the |my )?(?:wifi|bluetooth) status|what wifi am i (?:on|connected to)")
def _radio_status(m):
    dev = "bluetooth" if "bluetooth" in m.group(0) else "wifi"
    return (sysctl.wifi if dev == "wifi" else sysctl.bluetooth).call({"action": "status"})


# ---------------------------------------------------------------- display
@rule(r"(?:set (?:the )?)?(?:screen )?brightness (?:to |at )?" + NUM + r"(?: ?%| percent)?")
def _bright_set(m):
    return sysctl.brightness.call({"level": int(num(m.group(1)))})


@rule(r"(?:turn (?:the )?brightness up|brightness up|brighter|(?:make (?:it|the screen) )?brighter|increase (?:the )?brightness)")
def _bright_up(m):
    return sysctl.brightness.call({"change": 20})


@rule(r"(?:turn (?:the )?brightness down|brightness down|dimmer|dim (?:the )?screen|(?:make (?:it|the screen) )?darker|decrease (?:the )?brightness|lower (?:the )?brightness)")
def _bright_down(m):
    return sysctl.brightness.call({"change": -20})


@rule(r"(?:turn |switch )?(on|off) dark mode|(?:turn |switch )?dark mode (on|off)|(?:switch to |go |enable )(dark|light) mode|(enable|disable) dark mode|toggle dark mode")
def _dark(m):
    g = [x for x in m.groups() if x]
    if not g:
        return sysctl.dark_mode.call({"action": "toggle"})
    v = g[0]
    on = v in ("on", "dark", "enable")
    return sysctl.dark_mode.call({"action": "on" if on else "off"})


# ---------------------------------------------------------------- media
@rule(r"(?:play|resume)(?: (?:the )?music| music| something| my music)?|(?:start|resume) (?:the )?music")
def _play(m):
    return macos.media_control.call({"action": "play"})


@rule(r"pause|pause (?:the )?music|stop (?:the )?music")
def _pause(m):
    return macos.media_control.call({"action": "pause"})


@rule(r"(?:next|skip)(?: (?:song|track))?|skip (?:this )?(?:song|track)|play the next (?:song|track)")
def _next(m):
    return macos.media_control.call({"action": "next"})


@rule(r"previous(?: (?:song|track))?|(?:go )?back a (?:song|track)|play the (?:previous|last) (?:song|track)")
def _prev(m):
    return macos.media_control.call({"action": "previous"})


@rule(r"what(?:'s| is) (?:this song|playing|this track)|what song is (?:this|playing)")
def _now(m):
    return macos.media_control.call({"action": "now_playing"})


# ---------------------------------------------------------------- apps & system
def _app_exists(name: str) -> str | None:
    for base in ("/Applications", "/System/Applications", "/System/Applications/Utilities", str(Path.home() / "Applications")):
        for p in Path(base).glob("*.app"):
            if p.stem.lower() == name.lower():
                return p.stem
    return None


@rule(r"(?:open|launch|start) ([\w .&'-]{2,40}?)(?: app)?")
def _open(m):
    name = m.group(1).strip()
    app = _app_exists(name)
    if not app:
        return ""  # unknown → let the brain handle it ("open the jarvis project in claude code")
    return macos.open_app.call({"name": app})


@rule(r"lock(?: the)? (?:screen|mac|computer)|lock it")
def _lock(m):
    return macos.lock_screen.call({})


@rule(r"take a screenshot|screenshot|capture (?:the )?screen")
def _shot(m):
    return sysctl.take_screenshot.call({})


@rule(r"(?:how much |what(?:'s| is) (?:my |the )?)?battery(?: level| left| status)?|how(?:'s| is) (?:my |the )?battery")
def _battery(m):
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    mm = re.search(r"(\d+)%;\s*([\w ]+);", out)
    if not mm:
        return "I couldn't read the battery."
    state = mm.group(2).strip().replace("discharging", "on battery").replace("AC attached", "plugged in")
    return f"Battery is at {mm.group(1)} percent, {state}."


@rule(r"keep (?:the mac |it |the computer )?awake(?: for " + NUM + r" ?(minutes?|mins?|hours?))?|(?:don't|do not) (?:go to )?sleep")
def _awake(m):
    mins = 60
    if m.group(1):
        mins = int(num(m.group(1)) * (60 if m.group(2).startswith("h") else 1))
    return sysctl.keep_awake.call({"minutes": mins})


@rule(r"run (?:the |my )?(?:shortcut )?(.+?)(?: shortcut)?")
def _shortcut(m):
    name = m.group(1)
    if not re.search(r"shortcut", m.group(0)):
        return ""  # "run the tests" etc. → brain
    return sysctl.run_shortcut.call({"name": name})


@rule(r"open (?:the )?(wifi|bluetooth|sound|display|displays|battery|privacy|notifications|keyboard|network) settings")
def _settings(m):
    return sysctl.open_settings.call({"pane": m.group(1)})


# ---------------------------------------------------------------- notes & weather
@rule(r"(?:take a note|make a note|add a note|note down|write down|note)[: ,]+(?:(?:that|saying|says) )?(.{3,})")
def _note(m):
    return notes.add_note.call({"text": m.group(1)})


@rule(r"(?:what are |read |show |list )?my notes|read (?:me )?my notes")
def _notes(m):
    return notes.list_notes.call({"limit": 5})


@rule(r"what(?:'s| is) the weather(?: like)?(?: today| now| right now)?(?: in ([a-z .'-]{2,40}))?|weather(?: in ([a-z .'-]{2,40}))?"
      r"|(?:is it|will it be) (?:cold|hot|warm) (?:outside|today)")
def _weather(m):
    loc = next((g for g in m.groups() if g), "")
    out = weather.get_weather.call({"location": loc.strip(), "days": 1})
    if out.startswith("Error"):
        return out.replace("Error: ", "") if "no location" not in out else \
            "Tell me your city first: set city in config.yaml, or ask about the weather in a specific place."
    first = out.splitlines()[0]
    first = re.sub(r"^([^:]+): now ", r"In \1 it's ", first)
    return first.replace("°C", " degrees").replace("km/h", "kilometres an hour")


# ---------------------------------------------------------------- entry point
def handle(text: str) -> str | None:
    """Return a reply if `text` is a simple command handled locally, else None."""
    t = normalize(text)
    if not t or len(t) > 80:
        return None
    for pattern, fn in RULES:
        m = pattern.match(t)
        if m:
            try:
                reply = fn(m)
            except Exception as e:  # never let a local command crash the assistant
                return f"That didn't work: {e}"
            if reply:
                return reply
    return None
