"""Tool registry. The list order is deterministic (sorted by name) so the
tools block of every request is byte-identical and the prompt cache keeps hitting."""
from __future__ import annotations

from . import claude, macos, notes, phone, schedule, telegram, weather

_MODULES = [claude, macos, notes, phone, schedule, telegram, weather]

ALL_TOOLS = sorted((t for m in _MODULES for t in m.TOOLS), key=lambda t: t.name)
RISK = {k: v for m in _MODULES for k, v in m.RISK.items()}
BY_NAME = {t.name: t for t in ALL_TOOLS}

WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
