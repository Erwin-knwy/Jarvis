"""HTTP/WebSocket server: the Android (or any browser) client talks to Jarvis here.

Endpoints (all require the token: `Authorization: Bearer <t>` or `?token=<t>`):
  GET  /                 the PWA (token is checked by the API, not the page)
  POST /api/chat         {"text": "...", "session": "web"} -> {"reply": "..."}
  POST /api/transcribe   multipart audio file -> {"text": "..."}
  GET  /api/tts?text=    -> audio/wav in Jarvis's voice
  WS   /ws?token=        streaming chat + announcements
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import bus
from ..brain import get_brain
from ..voice.tts import _SENTENCE_END, Voice

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

_voice: Voice | None = None
_sockets: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(_app):
    global _loop
    _loop = asyncio.get_running_loop()

    def on_announce(text: str):
        msg = json.dumps({"type": "announce", "text": text})
        for ws in list(_sockets):
            asyncio.run_coroutine_threadsafe(ws.send_text(msg), _loop)

    unsubscribe = bus.subscribe(on_announce)
    yield
    unsubscribe()


app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _token() -> str:
    return os.environ.get("JARVIS_WEB_TOKEN", "")


def _authorized(token: str | None) -> bool:
    expected = _token()
    return bool(expected) and bool(token) and hmac.compare_digest(token, expected)


def _req_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return request.query_params.get("token")


def _require(request: Request):
    if not _authorized(_req_token(request)):
        raise HTTPException(401, "bad or missing token")


def voice() -> Voice:
    global _voice
    if _voice is None:
        _voice = Voice()
    return _voice


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/api/ping")
async def ping(request: Request):
    _require(request)
    return {"ok": True}


@app.post("/api/chat")
async def chat(request: Request):
    _require(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    reply = await asyncio.to_thread(
        get_brain().ask, text, session=body.get("session", "web"), channel="web"
    )
    return {"reply": reply}


@app.post("/api/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...)):
    _require(request)
    from ..voice.stt import shared

    data = await audio.read()
    try:
        text = await asyncio.to_thread(shared().transcribe_bytes, data)
    except Exception as e:
        log.exception("transcribe failed")
        return JSONResponse({"text": "", "error": str(e)}, status_code=422)
    return {"text": text}


@app.get("/api/tts")
async def tts(request: Request, text: str):
    _require(request)
    wav = await asyncio.to_thread(voice().wav_bytes, text[:1000])
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not _authorized(ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    _sockets.add(ws)
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "reset":
                get_brain().reset(msg.get("session", "web"))
                await ws.send_text(json.dumps({"type": "info", "text": "New conversation."}))
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            session = msg.get("session", "web")
            q: asyncio.Queue = asyncio.Queue()
            buf = {"s": ""}

            def on_text(chunk: str):
                loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": chunk})
                buf["s"] += chunk
                parts = _SENTENCE_END.split(buf["s"])
                for p in parts[:-1]:
                    if p.strip():
                        loop.call_soon_threadsafe(q.put_nowait, {"type": "sentence", "text": p.strip()})
                buf["s"] = parts[-1]

            def on_status(names: str):
                loop.call_soon_threadsafe(q.put_nowait, {"type": "status", "text": names})

            def work():
                try:
                    reply = get_brain().ask(text, session=session, channel="web",
                                            on_text=on_text, on_status=on_status)
                    if buf["s"].strip():
                        loop.call_soon_threadsafe(q.put_nowait, {"type": "sentence", "text": buf["s"].strip()})
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "text": reply})
                except Exception as e:  # never leave the client hanging
                    log.exception("ws turn failed")
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "text": f"Error: {e}"})

            loop.run_in_executor(None, work)
            while True:
                item = await q.get()
                await ws.send_text(json.dumps(item))
                if item["type"] == "done":
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _sockets.discard(ws)
