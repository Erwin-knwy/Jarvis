"""Your own Telegram account (not the bot), via Telethon / MTProto.

Lets Jarvis read your chats and reply as you. One-time login: `jarvis telegram-login`
(needs TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org → API development tools).
The login is stored in data/telegram_user.session — treat that file like a password."""
from __future__ import annotations

import asyncio
import difflib
import logging
import os
import threading
import time

from . import bus, config

log = logging.getLogger(__name__)

SESSION = config.DATA_DIR / "telegram_user"


def credentials() -> tuple[int, str] | None:
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID", ""), os.environ.get("TELEGRAM_API_HASH", "")
    if api_id.isdigit() and api_hash:
        return int(api_id), api_hash
    return None


def is_configured() -> bool:
    return credentials() is not None and SESSION.with_suffix(".session").exists()


def login() -> None:
    """Interactive login in the terminal (phone number, code, 2FA password)."""
    from telethon.sync import TelegramClient

    creds = credentials()
    if creds is None:
        print("First create an app at https://my.telegram.org → 'API development tools', then put\n"
              "TELEGRAM_API_ID=... and TELEGRAM_API_HASH=... in .env and run this again.")
        return
    with TelegramClient(str(SESSION), *creds) as client:
        me = client.get_me()
        print(f"Logged in as {me.first_name} (@{me.username}). Jarvis can now read and reply to your chats.")


class TelegramUser:
    """Runs Telethon on its own event loop thread; tools call it synchronously."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._dialogs: list = []
        self._dialogs_at = 0.0

    def start(self) -> None:
        threading.Thread(target=self._thread, daemon=True, name="telegram-user").start()
        self._ready.wait(30)

    def _thread(self):
        from telethon import TelegramClient, events

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        creds = credentials()
        if creds is None:
            self._error = "Telegram isn't linked (run `jarvis telegram-login`)."
            self._ready.set()
            return
        self._client = TelegramClient(str(SESSION), *creds, loop=self._loop)

        @self._client.on(events.NewMessage(incoming=True))
        async def on_message(event):
            try:
                if not event.is_private or not config.get().get("telegram", {}).get("announce_incoming", True):
                    return
                sender = await event.get_sender()
                if getattr(sender, "bot", False):
                    return
                name = _name(sender)
                text = event.raw_text or "(a photo or file)"
                bus.announce(f"Telegram message from {name}: {text[:300]}", push=False, title="Telegram")
            except Exception:
                log.exception("telegram announce failed")

        async def main():
            await self._client.connect()
            if not await self._client.is_user_authorized():
                self._error = "Telegram login expired (run `jarvis telegram-login`)."
            self._ready.set()
            await self._client.run_until_disconnected()

        try:
            self._loop.run_until_complete(main())
        except Exception as e:
            self._error = f"Telegram connection failed: {e}"
            log.exception("telegram user client stopped")
            self._ready.set()

    def run(self, coro_fn, timeout: float = 25):
        if self._error:
            raise RuntimeError(self._error)
        if self._client is None or self._loop is None:
            raise RuntimeError("Telegram isn't connected yet.")
        return asyncio.run_coroutine_threadsafe(coro_fn(self._client), self._loop).result(timeout)

    # ---------------------------------------------------------------- helpers
    async def _get_dialogs(self, client):
        if time.time() - self._dialogs_at > 60:
            self._dialogs = await client.get_dialogs(limit=300)
            self._dialogs_at = time.time()
        return self._dialogs

    async def find_chat(self, client, query: str):
        dialogs = await self._get_dialogs(client)
        q = query.lower().strip().lstrip("@")
        best, best_score = None, 0.0
        for d in dialogs:
            names = [d.name or ""]
            uname = getattr(d.entity, "username", None)
            if uname:
                names.append(uname)
            for n in names:
                nl = n.lower()
                score = 1.0 if nl == q else 0.9 if q in nl else difflib.SequenceMatcher(None, q, nl).ratio()
                if score > best_score:
                    best, best_score = d, score
        return best if best_score >= 0.6 else None


def _name(entity) -> str:
    if entity is None:
        return "someone"
    first = getattr(entity, "first_name", None) or getattr(entity, "title", None) or ""
    last = getattr(entity, "last_name", None) or ""
    return (first + " " + last).strip() or getattr(entity, "username", None) or "someone"


_instance: TelegramUser | None = None
_lock = threading.Lock()


def get() -> TelegramUser:
    global _instance
    with _lock:
        if _instance is None:
            _instance = TelegramUser()
            _instance.start()
        return _instance
