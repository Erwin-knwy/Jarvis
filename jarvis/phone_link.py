"""Link between the Mac and the Jarvis Phone Android app.

The phone keeps a WebSocket open to /phone. Over it:
- the Mac sends commands  {"id": "...", "cmd": "send_sms" | "call" | "recent_sms" | "status", ...}
  and the phone answers  {"id": "...", "ok": true, "result": "..."}
- the phone sends events {"type": "event", "event": "call" | "sms" | "call_ended", ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid

from . import bus, config

log = logging.getLogger(__name__)


class PhoneLink:
    def __init__(self):
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()
        self.device = ""

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # called from the server (async side)
    def attach(self, ws, loop, device: str = ""):
        with self._lock:
            self._ws, self._loop, self.device = ws, loop, device
        log.info("phone connected: %s", device)

    def detach(self, ws):
        with self._lock:
            if self._ws is ws:
                self._ws = None
                for f in self._pending.values():
                    if not f.done():
                        f.set_exception(ConnectionError("phone disconnected"))
                self._pending.clear()
        log.info("phone disconnected")

    def on_message(self, msg: dict):
        if "id" in msg and msg.get("type") != "event":
            f = self._pending.pop(msg["id"], None)
            if f and not f.done():
                f.set_result(msg)
            return
        if msg.get("type") == "event":
            self._on_event(msg)

    def _on_event(self, ev: dict):
        if not config.get().get("phone", {}).get("announce_on_mac", True):
            return
        kind = ev.get("event")
        if kind == "call":
            bus.announce(f"Incoming call on your phone from {ev.get('name') or 'an unknown number'}.",
                         push=False, title="Phone")
        elif kind == "sms":
            bus.announce(f"Text message from {ev.get('name') or 'an unknown number'}: {ev.get('body', '')[:300]}",
                         push=False, title="SMS")

    # called from tools (sync side)
    def request(self, cmd: dict, timeout: float = 20) -> dict:
        with self._lock:
            ws, loop = self._ws, self._loop
        if ws is None or loop is None:
            raise ConnectionError("The phone app isn't connected. Open Jarvis Phone and link it to this Mac.")
        rid = uuid.uuid4().hex[:12]

        async def send():
            fut = loop.create_future()  # futures must be created on the server's loop
            self._pending[rid] = fut
            await ws.send_text(json.dumps({"id": rid, **cmd}))
            return await asyncio.wait_for(fut, timeout)

        try:
            return asyncio.run_coroutine_threadsafe(send(), loop).result(timeout + 2)
        finally:
            self._pending.pop(rid, None)


link = PhoneLink()
