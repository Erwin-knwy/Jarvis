---
name: jarvis-doctor
description: Diagnose the Jarvis environment on this MacBook Pro 2015 (macOS 12): Python env, mic permission, models, API key, phone server, Telegram. Use when Jarvis won't start, audio fails, the phone can't connect, or before starting a new roadmap phase.
---

# Jarvis environment doctor

1. Run `./.venv/bin/jarvis doctor` and read the table. Fix each ✗:
   - **API key** → `jarvis setup` (prompts) or add `ANTHROPIC_API_KEY=` to `.env`.
   - **Mic permission** → System Preferences → Security & Privacy → Privacy → Microphone → enable the terminal app. (Inside Claude Code's sandboxed shell the mic is always blocked; that's expected, so ask the user to run it in their own terminal.)
   - **Wake word / Voice / Whisper / HTTPS cert / notifications** → `jarvis setup` (idempotent).
   - **Claude API** error → check the model name in `config.yaml` and the key's credit balance.
2. Logs: `data/jarvis.log` (and `data/service.*.log` when running as a launchd service).
3. Phone can't connect: the Mac and phone must be on the same Wi‑Fi (or Tailscale). The URL must be **https** with the right `?token=`. Check that the port (default 8765) is listening with `lsof -iTCP:8765 -sTCP:LISTEN`.
4. No sound: `jarvis say "test"`. If Piper fails, set `voice.tts_engine: say` in `config.yaml`.
5. Wake word too sensitive or deaf: tune `voice.wake_threshold` (0.3 to 0.7). Use `jarvis voice --ptt` to bypass it.
6. Don't install system packages or change permissions without asking the user.
