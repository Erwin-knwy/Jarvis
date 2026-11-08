"""Configuration: config.yaml + .env, with sane defaults."""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", ROOT / "data"))
MODELS_DIR = Path(os.environ.get("JARVIS_MODELS_DIR", ROOT / "models"))
CONFIG_PATH = Path(os.environ.get("JARVIS_CONFIG", ROOT / "config.yaml"))
ENV_PATH = ROOT / ".env"

DEFAULTS: dict = {
    "user_name": "sir",
    "city": "",
    "timezone": "",
    "brain": {
        "model": "claude-opus-5-5",
        "effort": "low",
        "max_tokens": 16000,
        "session_idle_minutes": 15,
        "web_search": True,
        "backend": "auto",
        "claude_code_model": "sonnet",
        "instant_commands": True,
    },
    "voice": {
        "wake_word": "hey_jarvis",
        "wake_threshold": 0.5,
        "tts_engine": "piper",
        "piper_voice": "en_GB-alan-medium",
        "piper_speaker": None,
        "speed": 1.0,
        "say_voice": "Daniel",
        "whisper_model": "base.en",
        "silence_ms": 800,
        "max_listen_s": 15,
        "follow_up_s": 5,
    },
    "server": {"host": "0.0.0.0", "port": 8765},
    "notifications": {"ntfy_topic": "", "ntfy_server": "https://ntfy.sh"},
    "briefing": {"enabled": True, "time": "08:00"},
    "telegram": {"announce_incoming": True},
    "phone": {"announce_on_mac": True},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get() -> dict:
    load_dotenv(ENV_PATH)
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg = _merge(DEFAULTS, yaml.safe_load(CONFIG_PATH.read_text()) or {})
    if os.environ.get("JARVIS_MODEL"):
        cfg["brain"]["model"] = os.environ["JARVIS_MODEL"]
    if os.environ.get("JARVIS_NTFY_TOPIC"):  # kept in .env: the topic name is the secret
        cfg["notifications"]["ntfy_topic"] = os.environ["JARVIS_NTFY_TOPIC"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return cfg


def reload() -> dict:
    get.cache_clear()
    return get()


def _yaml_scalar(value) -> str:
    return yaml.safe_dump(value, default_flow_style=True).strip().removesuffix("...").strip()


def set_value(dotted_key: str, value) -> None:
    """Persist one key into config.yaml, editing the line in place so comments survive."""
    parts = dotted_key.split(".")
    if CONFIG_PATH.exists() and len(parts) <= 2:
        lines = CONFIG_PATH.read_text().splitlines()
        section, key = (None, parts[0]) if len(parts) == 1 else parts
        in_section = section is None
        for i, line in enumerate(lines):
            if section is not None and not line.startswith((" ", "#")) and line.strip():
                in_section = line.split(":")[0].strip() == section
                continue
            indent = "  " if section else ""
            m = re.match(rf"^{indent}{re.escape(key)}:\s*([^#]*?)(\s+#.*)?$", line)
            if in_section and m and (section is None or line.startswith("  ")):
                lines[i] = f"{indent}{key}: {_yaml_scalar(value)}{m.group(2) or ''}"
                CONFIG_PATH.write_text("\n".join(lines) + "\n")
                reload()
                return
    data = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    data = data or {}
    node = data
    parts = dotted_key.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    reload()


def set_env(key: str, value: str) -> None:
    """Add or replace KEY=value in .env."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.environ[key] = value
