import types

import pytest

from jarvis import instant


class Recorder:
    def __init__(self):
        self.calls = []

    def tool(self, name):
        rec = self

        class T:
            def call(self, args):
                rec.calls.append((name, args))
                return f"ok:{name}"
        return T()


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    for mod_name, names in {
        "sysctl": ["wifi", "bluetooth", "brightness", "dark_mode", "volume", "keep_awake", "take_screenshot",
                   "run_shortcut", "open_settings"],
        "macos": ["set_volume", "media_control", "open_app", "lock_screen"],
        "schedule": ["set_timer", "list_reminders", "cancel_reminder"],
        "notes": ["add_note", "list_notes"],
    }.items():
        fake = types.SimpleNamespace(**{n: r.tool(n) for n in names})
        monkeypatch.setattr(instant, mod_name, fake)
    monkeypatch.setattr(instant, "_app_exists", lambda n: {"safari": "Safari", "telegram": "Telegram"}.get(n.lower()))
    return r


CASES = [
    ("Turn off the Wi-Fi.", ("wifi", {"action": "off"})),
    ("Hey Jarvis, could you turn on wifi please?", ("wifi", {"action": "on"})),
    ("wifi off", ("wifi", {"action": "off"})),
    ("Disable Bluetooth", ("bluetooth", {"action": "off"})),
    ("switch bluetooth on", ("bluetooth", {"action": "on"})),
    ("Is wifi on?", ("wifi", {"action": "status"})),
    ("Set the volume to 30", ("set_volume", {"level": 30})),
    ("volume fifty", ("set_volume", {"level": 50})),
    ("turn it up", ("volume", {"action": "up"})),
    ("Quieter", ("volume", {"action": "down"})),
    ("mute", ("volume", {"action": "mute"})),
    ("Brightness 40%", ("brightness", {"level": 40})),
    ("dim the screen", ("brightness", {"change": -20})),
    ("brighter", ("brightness", {"change": 20})),
    ("Turn on dark mode", ("dark_mode", {"action": "on"})),
    ("switch to light mode", ("dark_mode", {"action": "off"})),
    ("Set a timer for 5 minutes", ("set_timer", {"seconds": 300})),
    ("ten second timer", ("set_timer", {"seconds": 10})),
    ("timer for half an hour", ("set_timer", {"seconds": 1800})),
    ("cancel the timer", None),  # handled via DB lookup → "no timer running"
    ("Play music", ("media_control", {"action": "play"})),
    ("next song", ("media_control", {"action": "next"})),
    ("Pause.", ("media_control", {"action": "pause"})),
    ("What's playing?", ("media_control", {"action": "now_playing"})),
    ("Open Safari", ("open_app", {"name": "Safari"})),
    ("launch telegram", ("open_app", {"name": "Telegram"})),
    ("Lock the screen", ("lock_screen", {})),
    ("take a screenshot", ("take_screenshot", {})),
    ("keep the mac awake for 2 hours", ("keep_awake", {"minutes": 120})),
    ("run the focus shortcut", ("run_shortcut", {"name": "focus"})),
    ("open bluetooth settings", ("open_settings", {"pane": "bluetooth"})),
    ("Take a note: buy milk", ("add_note", {"text": "buy milk"})),
    ("what are my timers", ("list_reminders", {})),
]

NOT_INSTANT = [
    "Open the Jarvis project in Claude Code",
    "Run the tests",
    "Should I take an umbrella to my meeting in Paris tomorrow afternoon?",
    "Reply to Alex on Telegram saying I'm late",
    "Text mom I'm on my way",
    "Turn off the wifi and tell me a joke about routers while you're at it",
    "Open Photoshop",  # not installed → brain decides
    "What's the capital of Australia?",
]


@pytest.mark.parametrize("text,expected", CASES)
def test_instant_matches(rec, text, expected):
    out = instant.handle(text)
    assert out, f"not handled: {text}"
    if expected:
        assert rec.calls == [expected], f"{text} -> {rec.calls}"


@pytest.mark.parametrize("text", NOT_INSTANT)
def test_goes_to_brain(rec, text):
    assert instant.handle(text) is None, f"wrongly handled: {text} -> {rec.calls}"
    assert rec.calls == []


def test_time_and_date():
    assert instant.handle("What time is it?").startswith("It's")
    assert instant.handle("what's the date today").startswith("It's")
