import threading

import pytest
from fastapi.testclient import TestClient

from jarvis import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.use_path(tmp_path / "p.db")
    monkeypatch.setenv("JARVIS_WEB_TOKEN", "secret-token")
    from jarvis.server.app import app
    with TestClient(app) as c:
        yield c


def test_phone_tools_need_confirmation_and_connection():
    from jarvis.tools import phone
    assert "CONFIRMATION NEEDED" in phone.phone_send_sms.call({"contact": "Mom", "text": "hi"})
    assert "CONFIRMATION NEEDED" in phone.phone_call.call({"contact": "Mom"})
    out = phone.phone_send_sms.call({"contact": "Mom", "text": "hi", "confirmed": True})
    assert "isn't connected" in out


def test_phone_link_roundtrip(client):
    from jarvis.tools import phone
    from jarvis.phone_link import link
    result = {}
    with client.websocket_connect("/phone?token=secret-token&device=Pixel") as ws:
        assert link.connected and link.device == "Pixel"
        t = threading.Thread(target=lambda: result.update(
            out=phone.phone_send_sms.call({"contact": "Alex", "text": "On my way", "confirmed": True})))
        t.start()
        cmd = ws.receive_json()
        assert cmd["cmd"] == "send_sms" and cmd["contact"] == "Alex" and cmd["text"] == "On my way"
        ws.send_json({"id": cmd["id"], "ok": True, "result": "Text sent to Alex."})
        t.join(5)
    assert result["out"] == "Text sent to Alex."
    assert not link.connected


def test_phone_events_announce_on_mac(client, monkeypatch):
    from jarvis import bus
    monkeypatch.setattr(bus, "push_to_phone", lambda *a, **k: True)
    heard = []
    unsub = bus.subscribe(heard.append)
    with client.websocket_connect("/phone?token=secret-token") as ws:
        ws.send_json({"type": "event", "event": "call", "name": "Alex", "number": "+100"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    unsub()
    assert heard == ["Incoming call on your phone from Alex."]


def test_phone_ws_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/phone?token=nope") as ws:
            ws.receive_json()


def test_phone_setup_page(client):
    r = client.get("/phone-setup?token=secret-token")
    assert r.status_code == 200 and "jarvis://pair?url=" in r.text
    assert client.get("/phone-setup").status_code == 401


def test_claude_project_resolution(tmp_path, monkeypatch):
    from jarvis.tools import claude
    (tmp_path / "Jarvis").mkdir()
    (tmp_path / "website-2026").mkdir()
    monkeypatch.setattr(claude, "PROJECTS_DIR", tmp_path)
    assert claude._resolve_folder("jarvis").name == "Jarvis"
    assert claude._resolve_folder("website").name == "website-2026"
    assert claude._resolve_folder("") == tmp_path
    assert claude._resolve_folder("nothing-like-it") is None
    assert "Jarvis" in claude.list_projects.call({})


def test_telegram_tools_without_login(monkeypatch):
    from jarvis import tg_user
    from jarvis.tools import telegram
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.setattr(tg_user, "_instance", None)
    out = telegram.telegram_messages.call({})
    assert out.startswith("Error") and "telegram-login" in out
    assert "Error" in telegram.telegram_send_message.call({"chat": "x", "text": "y"})
