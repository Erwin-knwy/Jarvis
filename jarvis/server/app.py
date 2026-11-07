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


# ---------------------------------------------------------------- Jarvis Phone (Android app)
def cert_pin() -> str:
    """SHA-256 of the server certificate (the Android app pins it, so the self-signed cert is safe)."""
    import hashlib
    import ssl

    from .. import config

    cert = config.DATA_DIR / "cert.pem"
    if not cert.exists():
        return ""
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return hashlib.sha256(der).hexdigest()


def apk_path() -> Path:
    from .. import config
    return config.DATA_DIR / "jarvis-phone.apk"


@app.get("/phone-setup")
async def phone_setup(request: Request):
    _require(request)
    from urllib.parse import quote

    base = f"https://{request.headers.get('host', request.url.netloc)}"
    pair = f"jarvis://pair?url={quote(base, safe='')}&token={quote(_req_token(request) or '', safe='')}&pin={cert_pin()}"
    have_apk = apk_path().exists()
    html = f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Jarvis Phone setup</title><style>body{{background:#05080d;color:#d8e6f3;font:17px/1.5 -apple-system,Roboto,sans-serif;padding:24px;max-width:520px;margin:auto}}
a.b{{display:block;text-align:center;background:#38c6ff;color:#022;font-weight:600;padding:16px;border-radius:14px;text-decoration:none;margin:14px 0}}
small{{color:#6f8399}}</style></head><body>
<h2>Jarvis Phone</h2>
<p><b>1.</b> Install the app{'' if have_apk else ' (not built yet: run <code>jarvis build-phone</code> on the Mac)'}:</p>
<a class=b href="/download/jarvis-phone.apk?token={quote(_req_token(request) or '')}">Download Jarvis Phone</a>
<small>Android will ask to allow installs from Chrome. That's expected for apps outside the Play Store.</small>
<p><b>2.</b> Open the app once, grant the permissions, then come back here and tap:</p>
<a class=b href="{pair}">Link to this Mac</a>
</body></html>"""
    return Response(html, media_type="text/html")


@app.get("/download/jarvis-phone.apk")
async def download_apk(request: Request):
    _require(request)
    if not apk_path().exists():
        raise HTTPException(404, "APK not built yet - run `jarvis build-phone` on the Mac")
    return FileResponse(apk_path(), media_type="application/vnd.android.package-archive",
                        filename="jarvis-phone.apk")


@app.websocket("/phone")
async def phone_ws(ws: WebSocket):
    from ..phone_link import link

    if not _authorized(ws.query_params.get("token")):
        await ws.close(code=4401)
        return
    await ws.accept()
    link.attach(ws, asyncio.get_running_loop(), ws.query_params.get("device", "android"))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "ping":
                await ws.send_text('{"type":"pong"}')
                continue
            link.on_message(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("phone socket error")
    finally:
        link.detach(ws)
