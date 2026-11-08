"""Jarvis's brain on your Claude subscription, through Claude Code (`claude -p`).

No API key needed: Claude Code uses the account you're signed into (Pro/Max), and
requests count against that plan's usage limits. One Claude Code process stays open
per conversation (streaming JSON in and out), so follow-ups skip the start-up cost.
Jarvis's tools reach Claude Code through the local MCP server in jarvis.mcp_server.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import config, instant, mcp_server
from .brain import SYSTEM_PROMPT

log = logging.getLogger(__name__)

EXTRA_PROMPT = """
Your tools come from the "jarvis" MCP server. You can also search the web (WebSearch / WebFetch) for current facts.
You are not a coding assistant in this conversation: don't offer to write or edit code; you have no file access."""


def claude_bin() -> str | None:
    return shutil.which("claude") or next(
        (str(p) for p in [Path.home() / ".local/bin/claude", Path("/usr/local/bin/claude")] if p.exists()), None)


@dataclass
class CCSession:
    proc: subprocess.Popen | None = None
    last_used: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ClaudeCodeBrain:
    backend = "claude_code"

    def __init__(self):
        cfg = config.get()
        self.cfg = cfg["brain"]
        self.model = self.cfg.get("claude_code_model", "sonnet")
        self.system = SYSTEM_PROMPT.format(user=cfg.get("user_name") or "sir") + EXTRA_PROMPT
        self.workdir = config.DATA_DIR / "claude-workdir"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.mcp_config_path = config.DATA_DIR / "claude-mcp.json"
        self.sessions: dict[str, CCSession] = {}
        self._sessions_lock = threading.Lock()

    # ---------------------------------------------------------------- process
    def _spawn(self) -> subprocess.Popen:
        exe = claude_bin()
        if not exe:
            raise RuntimeError("Claude Code isn't installed")
        self.mcp_config_path.write_text(json.dumps(mcp_server.claude_mcp_config()))
        os.chmod(self.mcp_config_path, 0o600)
        cmd = [
            exe, "-p",
            "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
            "--model", self.model,
            "--system-prompt", self.system,
            "--tools", "WebSearch,WebFetch",
            "--mcp-config", str(self.mcp_config_path), "--strict-mcp-config",
            "--allowedTools", "mcp__jarvis", "WebSearch", "WebFetch",
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
        ]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY",)}  # use the subscription login
        log.info("starting Claude Code (%s)", self.model)
        self.errlog = config.DATA_DIR / "claude-code.log"
        err = open(self.errlog, "a")  # never a pipe: an undrained stderr pipe can freeze the process
        return subprocess.Popen(cmd, cwd=self.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=err, text=True, bufsize=1, env=env)

    def _session(self, sid: str) -> CCSession:
        idle = self.cfg.get("session_idle_minutes", 15) * 60
        with self._sessions_lock:
            s = self.sessions.get(sid)
            if s is not None and (time.time() - s.last_used > idle or (s.proc and s.proc.poll() is not None)):
                self._kill(s)
                s = None
            if s is None:
                s = self.sessions[sid] = CCSession()
            return s

    @staticmethod
    def _kill(s: CCSession):
        if s.proc and s.proc.poll() is None:
            try:
                s.proc.stdin.close()
                s.proc.terminate()
                s.proc.wait(3)
            except Exception:
                s.proc.kill()
        s.proc = None

    def reset(self, sid: str) -> None:
        with self._sessions_lock:
            s = self.sessions.pop(sid, None)
        if s:
            self._kill(s)

    def warmup(self, sid: str = "voice") -> None:
        """Start the Claude Code process in the background so the first question is faster."""
        def go():
            s = self._session(sid)
            with s.lock:
                if s.proc is None:
                    try:
                        s.proc = self._spawn()
                    except Exception:
                        log.exception("warmup failed")
        threading.Thread(target=go, daemon=True).start()

    # ---------------------------------------------------------------- main
    def ask(self, text: str, session: str = "default", channel: str = "voice",
            on_text: Callable[[str], None] | None = None,
            on_status: Callable[[str], None] | None = None) -> str:
        if config.get()["brain"].get("instant_commands", True):
            quick = instant.handle(text)
            if quick:
                if on_text:
                    on_text(quick)
                return quick
        s = self._session(session)
        with s.lock:
            s.last_used = time.time()
            now = datetime.now().strftime("%A %d %B %Y, %H:%M")
            prompt = f"[{now} | channel: {channel}]\n{text}"
            for attempt in (1, 2):
                try:
                    if s.proc is None or s.proc.poll() is not None:
                        s.proc = self._spawn()
                    reply = self._turn(s, prompt, on_text, on_status)
                    s.last_used = time.time()
                    return reply
                except _ProcessDied as e:
                    log.warning("Claude Code exited (%s), attempt %d", e, attempt)
                    self._kill(s)
                    if attempt == 2 or e.partial:
                        msg = e.user_message()
                        if on_text:
                            on_text(msg)
                        return msg
                except RuntimeError as e:
                    msg = f"I can't reach Claude Code: {e}."
                    if on_text:
                        on_text(msg)
                    return msg
        return ""

    def _turn(self, s: CCSession, prompt: str, on_text, on_status, timeout: float = 180) -> str:
        proc = s.proc
        try:
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise _ProcessDied(str(e), "", partial=False)
        parts: list[str] = []
        watchdog = threading.Timer(timeout, proc.kill)  # readline() can't time out by itself
        watchdog.start()
        try:
            return self._read_turn(proc, parts, on_text, on_status)
        finally:
            watchdog.cancel()

    def _read_turn(self, proc, parts, on_text, on_status) -> str:
        while True:
            line = proc.stdout.readline()
            if not line:
                err = ""
                try:
                    err = Path(self.errlog).read_text()[-800:]
                except OSError:
                    pass
                raise _ProcessDied("stream closed", err, partial=bool(parts))
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                e = ev.get("event", {})
                if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                    chunk = e["delta"]["text"]
                    parts.append(chunk)
                    if on_text:
                        on_text(chunk)
                elif e.get("type") == "content_block_start" and e.get("content_block", {}).get("type") in ("tool_use", "server_tool_use"):
                    name = e["content_block"].get("name", "").replace("mcp__jarvis__", "")
                    if on_status:
                        on_status(name)
                    if parts and not parts[-1].endswith((" ", "\n")):
                        parts.append(" ")
                        if on_text:
                            on_text(" ")
            elif kind == "result":
                if ev.get("is_error") and not parts:
                    msg = str(ev.get("result") or "Claude Code reported an error.")
                    log.warning("claude result error: %s", msg)
                    if "limit" in msg.lower():
                        msg = "I've hit your Claude plan's usage limit for now. It resets soon."
                    if on_text:
                        on_text(msg)
                    return msg
                return "".join(parts).strip() or str(ev.get("result") or "").strip()


class _ProcessDied(Exception):
    def __init__(self, reason: str, stderr: str, partial: bool):
        super().__init__(reason)
        self.stderr = stderr
        self.partial = partial

    def user_message(self) -> str:
        err = self.stderr.lower()
        if "login" in err or "auth" in err or "credential" in err:
            return "Claude Code isn't signed in. Run claude in a terminal and log in with your Claude account."
        return "Claude Code stopped unexpectedly. Please try again."
