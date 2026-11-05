# Jarvis

Personal voice assistant. README.md = usage; ROADMAP.md = phases; PLAN.md = build plan.

- Hub runs on a MacBook Pro 2015: macOS 12.7.6, i7‑4980HQ, 16 GB, Intel Iris Pro (no ML GPU). Keep local ML small and CPU‑only. `onnxruntime` is pinned to 1.19.2, the last release with Intel‑Mac wheels.
- Python 3.12 env lives in `.venv` (conda‑created). Run things with `./.venv/bin/python` / `./.venv/bin/jarvis`; tests: `./.venv/bin/python -m pytest -q`.
- Brain = Claude via `anthropic` SDK (`jarvis/brain.py`), manual streaming loop over `client.beta.messages.stream` with `@beta_tool` tools, server‑side fallbacks, prompt caching. Load the `claude-api` skill before changing it.
- History is append‑only per session (never edit/trim earlier turns). Sessions expire after idle time. Per‑turn context (time, channel) goes in the user message, never in the system prompt.
- Tool list must stay sorted/deterministic (`jarvis/tools/__init__.py`). New capabilities: follow the `jarvis-add-tool` skill. Environment problems: `jarvis doctor` / the `jarvis-doctor` skill.
- Known sandbox quirk: inside Claude Code's sandboxed shell, Python clients can't connect to local servers and the mic is blocked. Test the server in‑process with FastAPI `TestClient` and the voice loop with synthetic audio.
- `data/` (DB, logs, certs) and `models/` are git‑ignored. Secrets only in `.env`.
