"""Jarvis's brain: Claude + tools, streamed.

Design notes
- One conversation per session id ("voice", "web", "telegram:<id>"), append-only:
  we never edit or trim earlier turns (thinking blocks stay valid, the prompt
  cache keeps hitting). After `session_idle_minutes` of silence a session starts fresh;
  durable facts live in the memory tools instead.
- The system prompt and tool list are byte-stable. Per-turn context (time, channel)
  goes into the user message.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import anthropic

from . import config, offline
from .tools import ALL_TOOLS, BY_NAME, WEB_SEARCH

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Jarvis, a personal AI assistant running on {user}'s MacBook and reachable from their Android phone.

Personality: calm, competent, quietly witty British butler-engineer. Warm but never gushing. Address the user as "{user}" occasionally, not in every sentence.

How you speak:
- Most replies are heard through a speaker. Keep them short: one to three sentences unless asked for detail.
- Plain spoken English only: no markdown, bullet points, tables, emoji, URLs or code unless the channel is "web" or "telegram" and the content needs it.
- Say numbers, times and units naturally ("twenty past nine", "eighteen degrees").
- Don't announce which tool you are using; just do it and report the outcome.

How you act:
- Use your tools whenever they help. Prefer doing over explaining.
- Each user message starts with the current local time and channel in brackets; use that time to resolve "tomorrow", "in an hour", etc.
- Before answering personal questions, check memory with recall. When the user shares a lasting personal fact or says "remember", store it with remember.
- If a tool result says CONFIRMATION NEEDED, ask the user and only retry with confirmed=true after a clear yes.
- If something fails, say so briefly and suggest the fix.
- Use web search for current events, facts you are unsure of, prices, sports and news.
"""

MAX_STEPS = 12


@dataclass
class Session:
    messages: list = field(default_factory=list)
    last_used: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


class Brain:
    backend = "api"

    def __init__(self):
        cfg = config.get()
        self.cfg = cfg["brain"]
        self.client = anthropic.Anthropic(max_retries=2, timeout=120)
        self.system = SYSTEM_PROMPT.format(user=cfg.get("user_name") or "sir")
        self.tools = [t.to_dict() for t in ALL_TOOLS]
        if self.cfg.get("web_search", True):
            self.tools.append(WEB_SEARCH)
        self.sessions: dict[str, Session] = {}
        self._sessions_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4)

    # ---------------------------------------------------------------- sessions
    def _session(self, sid: str) -> Session:
        idle = self.cfg.get("session_idle_minutes", 15) * 60
        with self._sessions_lock:
            s = self.sessions.get(sid)
            if s is None or time.time() - s.last_used > idle:
                s = self.sessions[sid] = Session()
            return s

    def reset(self, sid: str) -> None:
        with self._sessions_lock:
            self.sessions.pop(sid, None)

    # ------------------------------------------------------------------- tools
    def _run_tool(self, block) -> dict:
        tool = BY_NAME.get(block.name)
        try:
            if tool is None:
                raise ValueError(f"unknown tool {block.name}")
            if not isinstance(block.input, dict):
                raise ValueError("tool input was not an object")
            out = tool.call(block.input)  # validates the input against the schema
            log.info("tool %s(%s) -> %s", block.name, block.input, str(out)[:200])
            return {"type": "tool_result", "tool_use_id": block.id, "content": str(out)}
        except Exception as e:
            log.warning("tool %s failed: %s", block.name, e)
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": f"Error: {e}", "is_error": True}

    # -------------------------------------------------------------------- main
    def ask(
        self,
        text: str,
        session: str = "default",
        channel: str = "voice",
        on_text: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Send one user utterance; stream reply text via on_text; return the full reply."""
        if config.get()["brain"].get("instant_commands", True):
            from . import instant
            quick = instant.handle(text)
            if quick:
                if on_text:
                    on_text(quick)
                return quick
        s = self._session(session)
        with s.lock:
            s.last_used = time.time()
            now = datetime.now().strftime("%A %d %B %Y, %H:%M")
            s.messages.append({"role": "user", "content": f"[{now} | channel: {channel}]\n{text}"})
            try:
                reply = self._loop(s, on_text, on_status)
            except (anthropic.AuthenticationError, TypeError) as e:
                # TypeError = the SDK found no credentials at all
                if isinstance(e, TypeError) and "authentication" not in str(e):
                    raise
                self._drop_dangling(s)
                reply = offline.handle(text) or (
                    "My Claude API key is missing or invalid. Run jarvis setup to add it. "
                    "Until then I can only do the time, timers, volume and notes.")
                if on_text:
                    on_text(reply)
            except (anthropic.APIConnectionError, anthropic.InternalServerError,
                    anthropic.RateLimitError) as e:
                log.warning("cloud brain unavailable: %s", e)
                self._drop_dangling(s)
                reply = offline.handle(text) or (
                    "I can't reach my brain in the cloud right now. "
                    "I can still do timers, the time, volume and notes."
                )
                if on_text:
                    on_text(reply)
            except anthropic.APIStatusError as e:
                log.error("API error %s: %s", e.status_code, e.message)
                self.reset(session)  # don't keep a broken history around
                reply = "Something went wrong talking to my brain; I've started a fresh conversation."
                if on_text:
                    on_text(reply)
            s.last_used = time.time()
            return reply

    def _drop_dangling(self, s: Session) -> None:
        """Remove the trailing user turn so a retry doesn't send two user turns in a row
        (only ever removes the newest, never-answered message - history stays append-only)."""
        if s.messages and s.messages[-1]["role"] == "user" and isinstance(s.messages[-1]["content"], str):
            s.messages.pop()

    def _loop(self, s: Session, on_text, on_status) -> str:
        reply_parts: list[str] = []
        json_retries = 0
        for _ in range(MAX_STEPS):
            try:
                with self.client.beta.messages.stream(
                    model=self.cfg["model"],
                    max_tokens=self.cfg.get("max_tokens", 16000),
                    system=self.system,
                    tools=self.tools,
                    messages=s.messages,
                    output_config={"effort": self.cfg.get("effort", "low")},
                    cache_control={"type": "ephemeral"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            reply_parts.append(event.text)
                            if on_text:
                                on_text(event.text)
                    msg = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # Tool-input JSON the SDK couldn't parse at all; re-issue the turn.
                json_retries += 1
                if json_retries > 2:
                    raise
                continue

            u = msg.usage
            log.info("turn: stop=%s in=%s cache_read=%s cache_write=%s out=%s",
                     msg.stop_reason, u.input_tokens, u.cache_read_input_tokens,
                     u.cache_creation_input_tokens, u.output_tokens)

            s.messages.append({"role": "assistant", "content": msg.content})

            if msg.stop_reason == "refusal":
                # Don't keep a refused exchange in the history.
                s.messages.clear()
                if not reply_parts:
                    t = "I'm afraid I can't help with that one."
                    reply_parts.append(t)
                    if on_text:
                        on_text(t)
                break
            if msg.stop_reason == "pause_turn":
                continue  # server tool (web search) paused; send it back to continue

            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            if not tool_uses:
                break
            if on_status:
                on_status(", ".join(b.name for b in tool_uses))
            if msg.stop_reason == "max_tokens":
                results = [{"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                            "content": "Error: tool input was cut off; try again with shorter input."}
                           for b in tool_uses]
            else:
                results = list(self._pool.map(self._run_tool, tool_uses))
            # All results go back in ONE user message.
            s.messages.append({"role": "user", "content": results})
            if reply_parts and not reply_parts[-1].endswith((" ", "\n")):
                reply_parts.append(" ")
                if on_text:
                    on_text(" ")
        return "".join(reply_parts).strip()


_brain: Brain | None = None
_brain_lock = threading.Lock()


def choose_backend() -> str:
    """"api" (Anthropic API key) or "claude_code" (your Claude subscription via Claude Code)."""
    import os
    want = config.get()["brain"].get("backend", "auto")
    if want in ("api", "claude_code"):
        return want
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    from .brain_cc import claude_bin
    return "claude_code" if claude_bin() else "api"


def get_brain():
    global _brain
    with _brain_lock:
        if _brain is None:
            if choose_backend() == "claude_code":
                from .brain_cc import ClaudeCodeBrain
                _brain = ClaudeCodeBrain()
            else:
                _brain = Brain()
        return _brain
