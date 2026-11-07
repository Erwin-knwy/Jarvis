# J.A.R.V.I.S.

A personal voice assistant for a **MacBook Pro 2015** and an **Android phone**, powered by Claude.

Say **"Hey Jarvis"** and talk. Jarvis replies in a calm British voice, and you can interrupt by saying "Hey Jarvis" again.
It sets timers and reminders, tracks notes, remembers facts about you, checks the weather, searches the web,
controls the Mac (apps, volume, Music, Calendar, Reminders) and pushes notifications to your phone.
On the phone you get an installable app with a hold-to-talk button that speaks back in the same voice,
plus an optional Telegram bot.

```
"Hey Jarvis" ─► openWakeWord ─► Silero VAD ─► faster-whisper ─► Claude + 30 tools ─► Piper voice
                       (all local on the Mac)                 (cloud)        (local, streamed per sentence)
Android app / Telegram ─► HTTPS + WebSocket ─┘
```

## Quick start

```bash
cd ~/Documents/GitHub/Jarvis
source .venv/bin/activate      # Python 3.12 env (already created)
jarvis setup                   # asks for your Claude API key, downloads models, makes tokens/certs
jarvis doctor                  # everything green?
jarvis run                     # voice + phone server + reminders (+ Telegram if configured)
```

Get an API key at <https://console.anthropic.com/settings/keys>. On first run, macOS asks for **Microphone** access for your
terminal (System Preferences → Security & Privacy → Privacy → Microphone). The Calendar, Reminders and Music
tools ask for **Automation** access the first time they're used.

## Commands

| Command | What it does |
|---|---|
| `jarvis run` | Everything: voice loop, phone/web server, scheduler, Telegram |
| `jarvis voice --ptt` | Voice only; press **Enter** to talk instead of the wake word |
| `jarvis chat [--speak]` | Type to Jarvis in the terminal (optionally hear replies) |
| `jarvis serve` | Phone/web server only (no microphone needed) |
| `jarvis voices` | Audition the bundled voices · `jarvis voices --set en_GB-semaine-medium:2` to switch |
| `jarvis say "text"` | Speak a line |
| `jarvis telegram-login` | Link your own Telegram account |
| `jarvis build-phone` / `jarvis phone-setup` | Build the Android app / get its install + link page |
| `jarvis install-service` | Start Jarvis automatically at login (launchd + `caffeinate`) |

Voice commands: "stop" / "never mind" cuts speech, and "new conversation" starts fresh. After answering, Jarvis listens
for 5 seconds without needing the wake word, so you can ask a follow-up.

## The voice

Default is **`en_GB-alan-medium`** (Piper), a measured British male voice and the closest free voice to the films.
Alternatives are already downloaded. Run `jarvis voices` to hear them all:

| Voice | Character |
|---|---|
| `en_GB-alan-medium` | calm, measured (default) |
| `en_GB-semaine-medium:2` | "Obadiah": deep and dry |
| `en_GB-semaine-medium:1` | "Spike": brisk |
| `en_GB-northern_english_male-medium` | warm Northern accent |
| `en_US-ryan-high` | American, high fidelity |

Any voice from the [Piper catalogue](https://huggingface.co/rhasspy/piper-voices) works: `jarvis voices --set <name>`.
Tune the pace with `voice.speed` in `config.yaml`. To use the macOS voice instead, set `tts_engine: say` (`say_voice: Daniel`).

## Android

The phone app runs on Jarvis's built-in HTTPS server. Phones need HTTPS for microphone access, so `jarvis setup` makes a certificate.

- **Same Wi-Fi:** run `jarvis serve` (or `jarvis run`). It prints a link like `https://192.168.x.x:8765/?token=…`.
  Open it in Chrome, tap *Advanced → Proceed*, then *⋮ → Add to Home screen*. Hold the glowing circle to talk.
- **Anywhere:** install [Tailscale](https://tailscale.com) on the Mac and the phone, then run
  `tailscale serve --bg https+insecure://localhost:8765` and open the `https://<mac>.<tailnet>.ts.net` address it shows
  (a real certificate, no warning). Add `?token=…` the first time.
- **Notifications** (timers, reminders, morning briefing): install the **ntfy** app and subscribe to the topic
  `jarvis setup` printed (`notifications.ntfy_topic` in `config.yaml`).
- **Telegram (optional):** create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN=…` in `.env`, message the bot once to
  learn your user ID, add it as `TELEGRAM_ALLOWED_USERS=…`, then restart Jarvis. Send text or voice notes, and get voice replies.

## Claude sessions & Telegram (on the Mac)

- **"Open a Claude Code session in Jarvis"** opens a Terminal running `claude` in `~/Documents/GitHub/Jarvis`.
  You can add a first prompt ("…and ask it to fix the failing tests"). "Open Claude" opens the desktop app,
  and "open Claude in the browser" opens claude.ai.
- **Telegram as you** ("any new Telegram messages?", "read my chat with Alex", "reply to Alex: running late"):
  1. Go to <https://my.telegram.org> → *API development tools* and create an app (any name).
  2. Run `jarvis telegram-login`, paste the `api_id`/`api_hash`, then your phone number and the code Telegram sends.
  Jarvis reads new private messages aloud (`telegram.announce_incoming`) and **always reads a reply back for your OK
  before sending**. The login lives in `data/telegram_user.session`, so treat that file like a password.
  "Open Telegram" just opens the app.

## Jarvis Phone (Android app): calls & texts

The web app can't see calls or SMS, so there's a small native app in `android/`:

- **Incoming call:** *"Sir, you have an incoming call from Alex."* Jarvis mutes the ringtone while
  speaking, then listens. Say **"answer"**, **"reject"**, or **"reject and text"** (sends your busy reply).
  If it hears nothing, the phone rings normally. The notification also has Answer / Reject / Reject + text buttons.
- **Incoming SMS:** *"Sir, a message from Mom: …  Would you like to reply?"* Say "yes", dictate the reply,
  and Jarvis reads it back and sends it after your "yes". You can also say "reply saying I'm on my way" in one go,
  or use the notification's Reply box.
- **Linked to the Mac** (optional): announcements use Jarvis's real voice, the Mac announces your calls too, and you can
  say to the Mac *"text Alex I'm on my way"*, *"call Mom"* or *"read my last texts"*.

**Install:**
1. `jarvis build-phone` builds the app (first run `scripts/fetch-android-toolchain.sh`; it downloads ~1 GB of Android tools into `~/.jarvis-toolchain`).
2. With `jarvis run` going, run `jarvis phone-setup` and open the printed link on the phone (same Wi-Fi).
   Tap **Download Jarvis Phone**, install it (allow installs from Chrome), and open it.
3. In the app: **Grant permissions**, then **Keep Jarvis running** (battery). If the SMS/call permissions are greyed out
   (Android 13+ restricts sideloaded apps), go to Settings → Apps → Jarvis Phone → ⋮ → *Allow restricted settings*.
4. Back on the setup page, tap **Link to this Mac**, then **Test the announcement**.

After a reboot, open the app once. Android only lets an app use the microphone in the background if it was
opened after boot, so until then the notification buttons still work but spoken answers don't.

## Configuration

`config.yaml` holds your name/title, home city (for weather), the model, voice settings and the briefing time. Secrets go in `.env`.

- **Model:** `claude-opus-5-5` at `effort: low` (best quality; roughly $25/month at ~100 requests/day).
  For about $1/month and faster replies set `brain.model: claude-haiku-5-5`.
- **Morning briefing:** `briefing.time: "08:00"`. Jarvis greets you with the weather, your calendar, reminders and headlines.
- **Offline:** without internet or an API key, Jarvis still handles the time, timers, volume and notes.

## Development

```bash
pip install -e ".[dev]" && pytest -q        # 14 tests, no API key needed
jarvis -v run                               # verbose logs (also in data/jarvis.log)
```

Code map: `jarvis/brain.py` (Claude loop), `jarvis/tools/` (capabilities; see the `jarvis-add-tool` skill),
`jarvis/voice/` (wake/VAD/STT/TTS), `jarvis/server/` (phone app + API), `jarvis/scheduler.py`, `jarvis/telegram_bot.py`.
Roadmap and phase plan: [ROADMAP.md](ROADMAP.md), [PLAN.md](PLAN.md).

## Contributing

Ideas, bug reports and pull requests are very welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
Licensed under the [MIT License](LICENSE).
