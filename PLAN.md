# Jarvis — Implementation Plan

Step‑by‑step build plan for the phases in [ROADMAP.md](ROADMAP.md). Each step ends with a check you can actually run.

## Target repo layout

```
Jarvis/
├── pyproject.toml            # deps + `jarvis` console script
├── .env.example              # ANTHROPIC_API_KEY=, JARVIS_MODEL=claude-opus-5-5, ...
├── config.yaml               # voice, wake word, model, effort, enabled tools
├── jarvis/
│   ├── __main__.py           # CLI: `jarvis chat | voice | serve | doctor`
│   ├── config.py
│   ├── brain.py              # Claude client, system prompt, tool runner, streaming
│   ├── memory.py             # SQLite: history, notes, long-term facts
│   ├── tools/                # one module per tool group, all @beta_tool functions
│   │   ├── __init__.py       # registry: ALL_TOOLS (sorted, deterministic → cacheable)
│   │   ├── basics.py         # time, date, timers
│   │   ├── notes.py
│   │   ├── weather.py        # Open‑Meteo (free, no key)
│   │   └── macos.py          # osascript wrappers (Phase 3)
│   ├── voice/
│   │   ├── wake.py           # openWakeWord "hey_jarvis"
│   │   ├── listen.py         # sounddevice capture + Silero VAD
│   │   ├── stt.py            # faster‑whisper
│   │   └── tts.py            # `say` / Piper, sentence queue, barge‑in
│   ├── server/               # Phase 4: FastAPI + WebSocket + PWA static files
│   └── scheduler.py          # Phase 5
├── web/                      # PWA (index.html, manifest.json, service worker)
└── tests/
```

---

## Phase 0: Foundations (1 day)

1. System deps: `brew install portaudio ffmpeg` (used by sounddevice and audio decoding). Expect source builds on macOS 12.
2. Python: `brew install python@3.12` (or `conda create -n jarvis python=3.12`) → `python3.12 -m venv .venv && source .venv/bin/activate`.
   Don't use the Anaconda base 3.13; some audio/ML wheels lag behind.
3. `pyproject.toml` with: `anthropic`, `python-dotenv`, `pyyaml`, `rich`, `typer`.
4. API key: create one at console.anthropic.com → put it in `.env` (git‑ignored).
5. `jarvis doctor` command: checks Python version, API key, mic access, ffmpeg, model reachability.

**Check:** `jarvis doctor` is all green.

## Phase 1: Text brain (1 week)

1. `brain.py`
   - `anthropic.Anthropic()` client, model from config (default `claude-opus-5-5`), `output_config={"effort": "low"}` for snappy chat.
   - Static system prompt (persona: concise, speaks in short sentences fit for TTS, asks before destructive actions). **No timestamps in it.** Give the current time through the `get_time` tool, or put it in the user turn.
   - `client.beta.messages.tool_runner(model=..., tools=ALL_TOOLS, messages=history, ...)` with streaming so text can go to TTS as it arrives.
   - Prompt caching: `cache_control={"type": "ephemeral"}` (top‑level auto‑caching). Keep the tool list order deterministic.
   - Handle `stop_reason == "refusal"` and API errors (`RateLimitError`, `APIConnectionError`) with a spoken fallback line.
2. Starter tools (`@beta_tool`, docstring = description):
   ```python
   from anthropic import beta_tool

   @beta_tool
   def add_note(text: str) -> str:
       """Save a note for the user.

       Args:
           text: The note content.
       """
       ...
   ```
   `get_time`, `get_weather(city)`, `add_note`, `list_notes`.
3. Server‑side **web search** tool: `{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}`.
4. `memory.py`: persist the conversation (full `response.content`, not just the text). Keep only the last N turns in context. Store facts the user asks Jarvis to remember.
5. `jarvis chat`: a REPL with rich output.

**Check:** "What's the weather in Tehran and save a note to buy umbrellas if it rains" → two tool calls, one reply. On the second turn, usage shows `cache_read_input_tokens > 0`.

## Phase 2: Voice on the Mac (1–2 weeks)

1. Deps: `sounddevice`, `numpy`, `openwakeword`, `onnxruntime`, `faster-whisper`, `silero-vad` (or `torch`‑free onnx variant).
2. Pipeline (asyncio, one process):
   `wake.py` (16 kHz frames, "hey_jarvis" score > threshold) → chime → `listen.py` records until 700 ms of silence (Silero VAD) → `stt.py` faster‑whisper `base.en` int8 (try `small.en` if accuracy is poor and latency allows) → `brain.py` streams text → `tts.py` splits it on sentence boundaries and speaks each sentence while the next one is still generating.
3. TTS v1: `say -v Daniel` (subprocess, killable). TTS v2: Piper `en_GB-alan-medium` or similar, played via sounddevice.
4. Barge‑in: keep the wake‑word detector running during playback; on detection, kill TTS and start listening.
5. Follow‑up mode: after Jarvis answers, listen for 5 s without needing the wake word.
6. Log per‑stage latency (wake→end‑of‑speech, STT, time to first token, first audio).

**Check:** time from end of speech to first audio is **< 2.5 s** on 10 sample queries. False wake‑ups are below 1 per hour with a TV on.

## Phase 3: Useful skills (2 weeks)

- `macos.py`: open/quit apps, set volume, Music play/pause/next, create Calendar events and Reminders, read the frontmost app, all through `osascript`. Grant mic, Automation and Accessibility permissions once.
- Timers/alarms (asyncio tasks, persisted in SQLite so they survive restarts).
- Long‑term memory: `remember(fact)` / `recall(query)` tools backed by SQLite FTS5.
- Safety layer: each tool declares `risk = "safe" | "confirm"`. `confirm` tools return a "needs confirmation" result until the user says yes. No raw shell tool; use allowlisted actions only.
- Unit tests for every tool (mock `osascript`).

**Check:** "Remind me tomorrow at 9 to call mom", "turn the volume down", "what did I tell you about my car?" all work by voice.

## Phase 4: Android (1–2 weeks)

1. `server/`: FastAPI + uvicorn. `POST /chat` (text) and `WS /ws` (streams tokens back). Simple bearer token auth.
2. Install **Tailscale** on the Mac and the phone. The phone reaches `http://<mac>.ts.net:8765` from anywhere, with no port forwarding.
3. `web/` PWA: one big push‑to‑talk button. The browser `SpeechRecognition` API (Chrome on Android) does STT. `speechSynthesis` speaks the streamed reply. "Add to Home screen" makes it look like an app.
4. Optional quick win: a **Telegram bot** (`python-telegram-bot`) that forwards messages to the same brain and replies with text or voice notes.
5. Share conversation history across clients (one SQLite DB).

**Check:** On mobile data (Wi‑Fi off), hold the button and ask "add milk to my notes". The note shows up when you ask the Mac.

## Phase 5: Proactive Jarvis (1 week)

- `scheduler.py` (APScheduler): morning briefing at 08:00 (weather, calendar, reminders, top news via web search) spoken on the Mac and pushed to the phone.
- Phone notifications via **ntfy** (self‑hostable, free Android app).
- Run the hub as a `launchd` agent so it starts at login, plus `caffeinate -s` while on power.

**Check:** the briefing arrives on the phone by itself for 3 days in a row.

## Phase 6: Offline / privacy mode (1–2 weeks)

- Install llama.cpp (build from source, Metal off) or Ollama if it supports macOS 12 at the time. Model: Llama 3.2 3B / Qwen 2.5 3B Q4 (~2 GB RAM).
- Router: obvious local intents (time, timers, volume, notes) skip the LLM entirely through a small rule/keyword matcher. When the network is down, other requests go to the local model with a reduced tool set.

**Check:** with Wi‑Fi off, "set a 5 minute timer" and "what time is it" still work.

## Phase 7: Stretch

Native Kotlin app using `VoiceInteractionService` (long‑press home → Jarvis) · Home Assistant integration via its REST API · camera input (Claude vision) · speaker ID · email/calendar via Google APIs.

---

## Claude Code skills for this project

- **Built in, already available:** `claude-api`. Use it whenever you touch `brain.py` (model IDs, tool runner, caching, error handling).
- **Installed in this repo** (`.claude/skills/`):
  - `jarvis-add-tool`: the checklist for adding a new tool (module, `@beta_tool`, risk level, registry, test).
  - `jarvis-doctor`: environment and hardware diagnostics for this Mac.
- Later: `/run` to launch and test the app, `/code-review` before merging each phase, `/schedule` if you want cloud routines.

## Decisions to revisit

1. **Model/cost:** Opus 5.5 at low effort (~$25/mo at heavy use) or Haiku 5.5 (~$1/mo, faster, less capable). Switch with `JARVIS_MODEL`.
2. **Phone UX:** PWA (Phase 4) is enough for most uses. A native app is only worth it if you want the hardware assistant button.
3. **Voice:** `say` costs nothing. Piper sounds noticeably better. Cloud TTS costs money but sounds best.
