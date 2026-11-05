import os

import pytest
from fastapi.testclient import TestClient

from jarvis import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.use_path(tmp_path / "s.db")
    monkeypatch.setenv("JARVIS_WEB_TOKEN", "secret-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # brain falls back to offline mode
    from jarvis.server.app import app
    with TestClient(app) as c:
        yield c


def test_auth_required(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/ping").status_code == 401
    assert client.get("/api/ping", headers={"Authorization": "Bearer secret-token"}).json() == {"ok": True}


def test_ws_stream(client):
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.send_json({"type": "chat", "text": "set a timer for 2 minutes"})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "done":
                break
        assert "Timer" in msgs[-1]["text"]
        assert any(m["type"] == "sentence" for m in msgs)
        ws.send_json({"type": "reset"})
        assert ws.receive_json()["type"] == "info"


def test_ws_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=nope") as ws:
            ws.receive_json()


def test_announce_reaches_ws(client):
    from jarvis import bus
    with client.websocket_connect("/ws?token=secret-token") as ws:
        bus.announce("Tea is ready.", push=False)
        assert ws.receive_json() == {"type": "announce", "text": "Tea is ready."}
