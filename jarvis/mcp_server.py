"""A tiny MCP server (Streamable HTTP, JSON responses) that exposes Jarvis's tools to
Claude Code. It runs inside the Jarvis process on 127.0.0.1 so tools share Jarvis's
state (phone link, Telegram session, timers). Each run gets a random access token."""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import socket
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .tools import ALL_TOOLS, BY_NAME

log = logging.getLogger(__name__)

SERVER_NAME = "jarvis"
TOKEN = secrets.token_urlsafe(24)
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def tool_list() -> list[dict]:
    out = []
    for t in ALL_TOOLS:
        d = t.to_dict()
        out.append({"name": d["name"], "description": d["description"], "inputSchema": d["input_schema"]})
    return out


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _dispatch(msg: dict) -> dict | None:
    method, req_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if req_id is None:  # notification (e.g. notifications/initialized): no response
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(req_id, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0"},
            "instructions": "Jarvis's own tools for the user's Mac, phone, Telegram, timers, notes and memory.",
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": tool_list()})
    if method == "tools/call":
        tool = BY_NAME.get(params.get("name", ""))
        if tool is None:
            return _error(req_id, -32602, f"Unknown tool {params.get('name')}")
        try:
            out = await asyncio.to_thread(tool.call, params.get("arguments") or {})
            text = str(out)
            log.info("mcp tool %s(%s) -> %s", tool.name, params.get("arguments"), text[:200])
            return _result(req_id, {"content": [{"type": "text", "text": text}],
                                    "isError": text.startswith("Error")})
        except Exception as e:
            return _result(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    return _error(req_id, -32601, f"Method not found: {method}")


@app.post("/mcp")
async def mcp(request: Request):
    if not hmac.compare_digest(request.headers.get("x-jarvis-token", ""), TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    if isinstance(body, list):
        replies = [r for r in [await _dispatch(m) for m in body] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await _dispatch(body)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@app.get("/mcp")
async def mcp_get():
    return Response(status_code=405)  # no server-initiated stream


@app.delete("/mcp")
async def mcp_delete():
    return Response(status_code=200)


# ---------------------------------------------------------------- lifecycle
_started: dict | None = None
_lock = threading.Lock()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ensure_running() -> dict:
    """Start the server once (background thread) and return its connection info."""
    global _started
    with _lock:
        if _started:
            return _started
        import time

        import uvicorn

        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name="jarvis-mcp").start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        _started = {"url": f"http://127.0.0.1:{port}/mcp", "token": TOKEN, "port": port}
        log.info("MCP server on %s", _started["url"])
        return _started


def claude_mcp_config() -> dict:
    info = ensure_running()
    return {"mcpServers": {SERVER_NAME: {"type": "http", "url": info["url"],
                                         "headers": {"X-Jarvis-Token": info["token"]}}}}
