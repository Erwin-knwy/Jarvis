import types
from datetime import datetime, timedelta

import pytest

from jarvis import db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.use_path(tmp_path / "test.db")
    yield


# ------------------------------------------------------------------ tools
def test_notes_roundtrip():
    from jarvis.tools import notes
    assert "Saved note #1" in notes.add_note.call({"text": "buy milk"})
    assert "buy milk" in notes.list_notes.call({})
    assert "CONFIRMATION NEEDED" in notes.delete_note.call({"note_id": 1})
    assert "buy milk" in notes.list_notes.call({})
    assert "Deleted" in notes.delete_note.call({"note_id": 1, "confirmed": True})
    assert notes.list_notes.call({}) == "No notes found."


def test_memory_search():
    from jarvis.tools import notes
    notes.remember.call({"fact": "The user's car is a blue Honda Civic."})
    notes.remember.call({"fact": "The user's sister is called Sara."})
    assert "Honda" in notes.recall.call({"query": "what car do I drive"})
    assert "Sara" in notes.recall.call({"query": "sister"})
    assert "Nothing relevant" in notes.recall.call({"query": "zebra"})


def test_timer_and_reminder():
    from jarvis.tools import schedule
    assert "Timer #1" in schedule.set_timer.call({"minutes": 5, "label": "tea"})
    when = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    assert "Reminder #2" in schedule.set_reminder.call({"text": "call mom", "when": when})
    assert "in the past" in schedule.set_reminder.call({"text": "x", "when": "2001-01-01T00:00:00"})
    listing = schedule.list_reminders.call({})
    assert "tea" in listing and "call mom" in listing
    assert "Cancelled" in schedule.cancel_reminder.call({"reminder_id": 1})
    assert "tea" not in schedule.list_reminders.call({})


def test_tool_input_validation():
    from jarvis.tools import schedule
    with pytest.raises(ValueError):
        schedule.set_timer.call({"minutes": "lots"})


def test_scheduler_fires(monkeypatch):
    from jarvis import bus, scheduler
    said = []
    monkeypatch.setattr(bus, "push_to_phone", lambda *a, **k: True)
    unsub = bus.subscribe(said.append)
    with db.tx() as c:
        c.execute("INSERT INTO reminders(text, due_at, kind) VALUES ('tea', ?, 'timer')",
                  ((datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds"),))
    scheduler.Scheduler()._fire_due()
    unsub()
    assert said == ["Your tea timer is done."]


def test_offline_router(monkeypatch):
    from jarvis import offline
    assert offline.handle("what time is it").startswith("It's")
    assert "Timer" in offline.handle("set a timer for 10 minutes")
    assert "Saved note" in offline.handle("take a note buy batteries")
    assert offline.handle("tell me a joke") is None


def test_clean_for_speech():
    from jarvis.voice.tts import clean_for_speech
    assert clean_for_speech("**Hi** see [docs](http://x.y) — 20°C, 50%") == "Hi see docs — 20 degrees, 50 percent"


def test_tool_registry_is_sorted_and_unique():
    from jarvis.tools import ALL_TOOLS
    names = [t.name for t in ALL_TOOLS]
    assert names == sorted(names) and len(names) == len(set(names))
    for t in ALL_TOOLS:
        d = t.to_dict()
        assert d["description"] and d["input_schema"]["type"] == "object"


# ------------------------------------------------------------------ brain loop with a fake client
def _block(**kw):
    return types.SimpleNamespace(**kw)


class FakeStream:
    def __init__(self, msg, texts):
        self.msg, self.texts = msg, texts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for t in self.texts:
            yield _block(type="text", text=t)

    def get_final_message(self):
        return self.msg


def _msg(stop, content):
    usage = _block(input_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=5)
    return _block(stop_reason=stop, content=content, usage=usage)


def test_brain_tool_loop(monkeypatch):
    from jarvis import brain as brain_mod
    b = brain_mod.Brain.__new__(brain_mod.Brain)
    brain_mod.Brain.__init__(b)
    calls = []
    script = [
        (_msg("tool_use", [_block(type="tool_use", id="t1", name="add_note", input={"text": "eggs"})]), []),
        (_msg("end_turn", [_block(type="text", text="Noted, sir.")]), ["Noted, ", "sir."]),
    ]

    def fake_stream(**kw):
        calls.append(kw)
        return FakeStream(*script[len(calls) - 1])

    b.client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=fake_stream)))
    out = []
    reply = b.ask("note eggs", session="t", on_text=out.append)
    assert reply == "Noted, sir."
    assert "".join(out) == "Noted, sir."
    # history: user, assistant(tool_use), user(tool_result), assistant(text)
    msgs = b.sessions["t"].messages
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2]["content"][0]["type"] == "tool_result" and "Saved note" in msgs[2]["content"][0]["content"]
    # request shape: stable system/tools, caching + fallbacks on
    assert calls[0]["cache_control"] == {"type": "ephemeral"}
    assert calls[0]["fallbacks"] == "default"
    assert calls[0]["system"] == calls[1]["system"] and calls[0]["tools"] == calls[1]["tools"]


def test_brain_refusal_resets(monkeypatch):
    from jarvis import brain as brain_mod
    b = brain_mod.Brain()
    b.client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(
        stream=lambda **kw: FakeStream(_msg("refusal", []), []))))
    reply = b.ask("bad thing", session="r")
    assert "can't help" in reply
    assert b.sessions["r"].messages == []
