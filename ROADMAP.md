# Jarvis — Roadmap

A personal voice assistant that runs on a **MacBook Pro 2015** and an **Android phone**.

## Hardware reality check

| | MacBook Pro 2015 (this machine) | Android phone |
|---|---|---|
| CPU / GPU | i7‑4980HQ (4c/8t, Haswell), Intel Iris Pro, **no usable ML GPU** | Varies; has fast on‑device STT/TTS built in |
| RAM / disk | 16 GB / ~130 GB free | — |
| OS | macOS 12.7.6 Monterey (last supported; Homebrew may build some formulae from source) | Android |
| Good at | Running a Python server 24/7, wake word, small Whisper models, macOS automation | Mic + speaker you carry around, native speech APIs |
| Bad at | Running a large local LLM (a 3B model gives ~5–10 tokens/s on CPU; 7B+ is too slow to talk to) | Running Python full‑time |

**What that means:** the hard thinking goes to a **cloud LLM (Claude API)**. Audio processing that has to be fast and private stays **on the device**: wake word, voice activity detection, speech‑to‑text and text‑to‑speech. The Mac is the **hub**. The phone is a **thin client** that connects to it.

## Architecture

```
                ┌──────────────────────── MacBook (hub) ────────────────────────┐
  mic ──► wake word ──► VAD ──► speech‑to‑text ─┐                               │
 (openWakeWord        (Silero)  (faster‑whisper) │                               │
  "hey jarvis")                                  ▼                               │
                                        ┌──────────────┐   tools: time, weather, │
  Android PWA ──WebSocket (Tailscale)──►│  Jarvis Core │◄─ notes, reminders,     │
  Telegram bot ───────────────────────► │ Claude API + │   macOS (AppleScript),  │
                                        │ tool runner  │   web search, memory,   │
                                        └──────┬───────┘   scheduler, Home Asst. │
                                               ▼                                 │
  speaker ◄── text‑to‑speech (macOS `say` → Piper) ◄── streamed sentences          │
                └────────────────────────────────────────────────────────────────┘
```

## Status (2026-11-05)

Phases 0–5 are **built** (see README.md): text and voice brain, 22 tools, wake word + barge‑in + follow‑up,
the Piper "Jarvis" voice, Android PWA over HTTPS/Tailscale, Telegram bot, ntfy push, reminders/timers,
morning briefing, launchd service. Phase 6 is partly done (a rule‑based offline mode for time, timers, volume and notes; no local LLM yet).
Remaining: measure real latency with a live mic (Phase 2 target is under 2.5 s), the Phase 6 local LLM, and Phase 7.

## Phases

| # | Phase | Outcome ("done when…") | Est. |
|---|---|---|---|
| 0 | **Foundations** | venv, API key, repo skeleton, config, logging; `jarvis --help` runs | 1 day |
| 1 | **Text brain** | Terminal chat with Claude: streaming replies, tool runner, 3 starter tools (time, weather, notes), conversation memory, prompt caching confirmed by `cache_read_input_tokens > 0` | 1 week |
| 2 | **Voice on the Mac** | Say "Hey Jarvis", ask something, hear the answer start in **< 2.5 s**; barge‑in (talking over it) stops speech | 1–2 weeks |
| 3 | **Useful skills** | Mac control (apps, volume, Music, Calendar/Reminders via AppleScript), timers/alarms, web search, long‑term memory ("remember that…"), confirmation before risky actions | 2 weeks |
| 4 | **Android** | FastAPI + WebSocket server; installable PWA on the phone (push‑to‑talk, Web Speech API for STT/TTS); reachable anywhere through Tailscale; optional Telegram bot | 1–2 weeks |
| 5 | **Proactive Jarvis** | Morning briefing, scheduled reminders, push notifications to the phone (ntfy), a daily summary | 1 week |
| 6 | **Offline / privacy mode** | Local LLM (llama.cpp/Ollama, 1–3B model) answers simple requests and keeps working when offline; a router decides local vs. cloud | 1–2 weeks |
| 7 | **Stretch** | Native Android app (Kotlin) registered as the phone's default assistant; Home Assistant smart‑home control; camera/vision ("what am I looking at?"); voice ID for multiple users | open |

## Key technology choices

| Concern | Choice | Why | Alternatives |
|---|---|---|---|
| Language | **Python 3.12** (venv, not the Anaconda base env) | Best ecosystem for audio/ML; matches every library below | Node/TS |
| Brain | **Claude API**, `anthropic` SDK, **tool runner** (`client.beta.messages.tool_runner` + `@beta_tool`) | No custom agent loop to maintain; strong tool use; the old Mac can't run a good LLM locally | Local LLM (Phase 6) |
| Model | `claude-opus-5-5` by default with `effort: "low"` for chat. Model name lives in config | Best quality. Lower effort keeps latency and cost down for voice | `claude-haiku-5-5` (much cheaper and faster) or `claude-sonnet-5-5`; your call, it's one config line |
| Wake word | **openWakeWord** (ships a pretrained **"hey jarvis"** model) | Free, offline, light on CPU | Porcupine (needs an account key) |
| VAD | Silero VAD | Accurate end‑of‑speech detection | webrtcvad |
| Speech‑to‑text | **faster‑whisper** `base.en`/`small.en`, int8 on CPU | Runs locally at usable speed on Haswell | whisper.cpp; cloud STT |
| Text‑to‑speech | macOS `say` first, then **Piper** (neural, fast on CPU) | Zero setup → natural voice | ElevenLabs/OpenAI TTS (cloud, paid) |
| Phone client | **PWA** served by the Mac + Tailscale | No app‑store work; uses the phone's own STT/TTS | Telegram bot (fastest to build), native Kotlin (Phase 7) |
| Storage | SQLite (notes, reminders, memories, chat log) | Zero ops | — |
| Scheduling | APScheduler | In‑process cron | launchd |

## Rough running cost (Claude API, list prices)

Assume ~100 voice turns a day, a ~3k‑token cached system prompt and tool list, ~500 new input tokens and ~300 output tokens per turn:

- **Opus 5.5** ($4 in / $20 out / $0.20 cached read per M tokens): ≈ **$0.85/day ≈ $25/month**
- **Haiku 5.5** ($0.10 in / $0.50 out per M tokens): ≈ **< $1/month**

Prompt caching is what keeps these numbers low. Freeze the system prompt and tool list, and never put timestamps in them.

## Risks

- **Latency**: streaming Claude output and speaking sentence by sentence is mandatory. Measure every stage (Phase 2 metric).
- **macOS 12 + Homebrew**: some formulae build from source. Prefer pip wheels (faster‑whisper, onnxruntime) where possible.
- **Mac sleeping**: when the lid is closed the hub is offline. Use `caffeinate`/Energy settings, and accept that the phone falls back to "hub unreachable" (or Phase 6 offline mode).
- **Safety**: tools that act on the computer (shell, AppleScript, email) need an allowlist and spoken confirmation for anything destructive.
- **Secrets**: `.env` only, git‑ignored.
