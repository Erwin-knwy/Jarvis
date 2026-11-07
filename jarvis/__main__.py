"""`jarvis` command line.

  jarvis setup             download models, create tokens/certs, ask for the API key
  jarvis doctor            check the environment
  jarvis chat [--speak]    talk by typing
  jarvis voice [--ptt]     talk out loud ("Hey Jarvis", or Enter for push-to-talk)
  jarvis serve             phone/web server only
  jarvis telegram          Telegram bot only
  jarvis run               everything: voice + server + scheduler + telegram
  jarvis voices [--set N]  audition / choose Jarvis's voice
  jarvis say TEXT          speak a line
  jarvis telegram-login    link your own Telegram account (read/reply as you)
  jarvis build-phone       build the Jarvis Phone Android app
  jarvis phone-setup       link to install + pair the phone app
  jarvis install-service   start Jarvis automatically at login (launchd)
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
from pathlib import Path

from . import config

VOICE_CHOICES = {
    "en_GB-alan-medium": "British male, calm and measured (default — closest to the films)",
    "en_GB-northern_english_male-medium": "British male, Northern accent, warm",
    "en_GB-semaine-medium:2": "British male 'Obadiah', deep and dry",
    "en_GB-semaine-medium:1": "British male 'Spike', brisk",
    "en_US-ryan-high": "American male, high quality",
}

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def _log_setup(verbose: bool):
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.DATA_DIR / "jarvis.log"), logging.StreamHandler()]
        if verbose else [logging.FileHandler(config.DATA_DIR / "jarvis.log")],
    )
    if not verbose:  # still log INFO to the file
        logging.getLogger().setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram", "faster_whisper", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _console():
    from rich.console import Console
    return Console()


def lan_ip() -> str:
    # Prefer the real Wi-Fi/Ethernet interface (VPNs hijack the default route).
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True,
                                text=True, timeout=2).stdout.strip()
            if ip:
                return ip
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ---------------------------------------------------------------- setup
def download_voice(name: str) -> Path:
    import httpx

    d = config.MODELS_DIR / "voices"
    d.mkdir(parents=True, exist_ok=True)
    lang_region, voice_name, quality = name.split("-")
    lang = lang_region.split("_")[0]
    for ext in (".onnx", ".onnx.json"):
        dest = d / f"{name}{ext}"
        if dest.exists():
            continue
        url = f"{PIPER_BASE}/{lang}/{lang_region}/{voice_name}/{quality}/{name}{ext}?download=true"
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
            tmp.rename(dest)
    return d / f"{name}.onnx"


def make_cert() -> tuple[Path, Path]:
    cert, key = config.DATA_DIR / "cert.pem", config.DATA_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    ip = lan_ip()
    host = socket.gethostname()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "825", "-subj", "/CN=jarvis",
         "-addext", f"subjectAltName=DNS:localhost,DNS:{host},IP:127.0.0.1,IP:{ip}"],
        check=True, capture_output=True,
    )
    return cert, key


def cmd_setup(args):
    c = _console()
    cfg = config.get()
    c.rule("[bold cyan]Jarvis setup")

    # API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        c.print("Jarvis needs a Claude API key: https://console.anthropic.com/settings/keys")
        if sys.stdin.isatty():
            import getpass
            key = getpass.getpass("Paste your ANTHROPIC_API_KEY (input hidden, Enter to skip): ").strip()
            if key:
                config.set_env("ANTHROPIC_API_KEY", key)
                c.print("[green]✓ saved to .env")
    else:
        c.print("[green]✓ API key present")

    # tokens
    if not os.environ.get("JARVIS_WEB_TOKEN"):
        config.set_env("JARVIS_WEB_TOKEN", secrets.token_urlsafe(18))
    c.print("[green]✓ web token ready")
    if not cfg["notifications"].get("ntfy_topic"):
        config.set_env("JARVIS_NTFY_TOPIC", "jarvis-" + secrets.token_hex(8))
        config.reload()
    c.print(f"[green]✓ phone notification topic: {config.get()['notifications']['ntfy_topic']}")

    # models
    with c.status("Downloading wake word model…"):
        import openwakeword.utils
        openwakeword.utils.download_models([cfg["voice"]["wake_word"]])
    c.print("[green]✓ wake word 'hey jarvis'")
    voices = [cfg["voice"]["piper_voice"]] + ([] if args.minimal else
                                              sorted({v.split(":")[0] for v in VOICE_CHOICES}))
    for v in dict.fromkeys(voices):
        with c.status(f"Downloading voice {v}…"):
            download_voice(v)
        c.print(f"[green]✓ voice {v}")
    with c.status(f"Downloading speech recognition ({cfg['voice']['whisper_model']})…"):
        from .voice.stt import Transcriber
        Transcriber().warmup()
    c.print("[green]✓ whisper")

    try:
        make_cert()
        c.print("[green]✓ HTTPS certificate for the phone app")
    except Exception as e:
        c.print(f"[yellow]! couldn't create HTTPS cert ({e}); the phone mic needs HTTPS")

    config.reload()
    c.rule("[bold cyan]Done")
    _print_phone_help(c)
    c.print("\nNext: [bold]jarvis doctor[/], then [bold]jarvis run[/] and say “Hey Jarvis”.")


def _print_phone_help(c):
    from dotenv import load_dotenv
    load_dotenv(config.ENV_PATH, override=True)
    port = config.get()["server"]["port"]
    tok = os.environ.get("JARVIS_WEB_TOKEN", "")
    c.print("\n[bold]Android:[/]")
    c.print(f"  1. Same Wi-Fi: open [cyan]https://{lan_ip()}:{port}/?token={tok}[/] in Chrome,")
    c.print("     tap Advanced → Proceed (self-signed certificate), then ⋮ → Add to Home screen.")
    c.print("  2. Anywhere: install Tailscale on Mac + phone, run "
            f"[cyan]tailscale serve --bg https+insecure://localhost:{port}[/] and open the https://…ts.net URL.")
    c.print(f"  3. Notifications: install the ntfy app and subscribe to topic "
            f"[cyan]{config.get()['notifications']['ntfy_topic']}[/].")


# ---------------------------------------------------------------- doctor
def cmd_doctor(args):
    from rich.table import Table
    c = _console()
    t = Table(title="Jarvis doctor")
    t.add_column("Check")
    t.add_column("")
    t.add_column("Details")
    mic_stuck = False

    def row(name, ok, detail=""):
        t.add_row(name, "[green]✓" if ok else "[red]✗", detail)

    import platform
    row("Python", sys.version_info[:2] >= (3, 11), platform.python_version())
    row("API key", bool(os.environ.get("ANTHROPIC_API_KEY")),
        "set" if os.environ.get("ANTHROPIC_API_KEY") else "run `jarvis setup` or add to .env")
    cfg = config.get()
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        row("Microphone", True, dev["name"])
        import numpy as np
        peak = {"v": None}

        def _probe():
            r = sd.rec(8000, samplerate=16000, channels=1, dtype="int16")
            sd.wait()
            peak["v"] = int(np.abs(r).max())

        th = threading.Thread(target=_probe, daemon=True)
        th.start()
        th.join(5)
        lvl = peak["v"]
        if lvl is None:
            mic_stuck = True
            row("Mic permission", False, "no audio after 5 s: macOS may be showing a permission prompt, "
                "or the terminal lacks Microphone access (System Preferences → Security & Privacy)")
        else:
            row("Mic permission", lvl > 0, f"peak level {lvl}" if lvl else
                "silent: allow your terminal in System Preferences → Security & Privacy → Microphone")
    except Exception as e:
        row("Microphone", False, str(e))
    from openwakeword.utils import download_models  # noqa: F401
    import openwakeword
    oww = Path(openwakeword.__file__).parent / "resources/models" / f"{cfg['voice']['wake_word']}_v0.1.onnx"
    row("Wake word model", oww.exists(), oww.name)
    voice = config.MODELS_DIR / "voices" / f"{cfg['voice']['piper_voice']}.onnx"
    row("Voice", voice.exists() or cfg["voice"]["tts_engine"] == "say",
        f"{cfg['voice']['tts_engine']}: {cfg['voice']['piper_voice']}")
    wdir = config.MODELS_DIR / "whisper"
    row("Whisper", any(wdir.glob(f"*{cfg['voice']['whisper_model']}*")) if wdir.exists() else False,
        cfg["voice"]["whisper_model"])
    row("HTTPS cert", (config.DATA_DIR / "cert.pem").exists(), "needed for the phone microphone")
    row("Phone notifications", bool(cfg["notifications"].get("ntfy_topic")),
        cfg["notifications"].get("ntfy_topic") or "run `jarvis setup`")
    row("Telegram", bool(os.environ.get("TELEGRAM_BOT_TOKEN")), "optional")
    if os.environ.get("ANTHROPIC_API_KEY"):
        import time
        import anthropic
        try:
            t0 = time.time()
            anthropic.Anthropic().messages.create(
                model=cfg["brain"]["model"], max_tokens=16,
                messages=[{"role": "user", "content": "Say OK."}],
                output_config={"effort": "low"},
            )
            row("Claude API", True, f"{cfg['brain']['model']} answered in {time.time() - t0:.1f}s")
        except Exception as e:
            row("Claude API", False, f"{type(e).__name__}: {str(e)[:120]}")
    c.print(t)
    if mic_stuck:  # a blocked PortAudio call would hang interpreter shutdown
        sys.stdout.flush()
        os._exit(1)


# ---------------------------------------------------------------- chat / voice
def _require_key(c):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        c.print("[yellow]No ANTHROPIC_API_KEY yet — only offline commands (time, timers, volume, notes) "
                "will work. Run `jarvis setup` to add one.")


def cmd_chat(args):
    from .brain import get_brain
    from .scheduler import Scheduler
    from . import bus
    c = _console()
    _require_key(c)
    brain = get_brain()
    speaker = None
    if args.speak:
        from .voice.tts import Speaker
        speaker = Speaker()
    bus.subscribe(lambda t: (c.print(f"[bold magenta]Jarvis:[/] {t}"), speaker and speaker.say(t)))
    Scheduler(get_brain).start()
    c.print("[bold cyan]Jarvis[/] — type to chat, /new for a fresh conversation, Ctrl+D to quit.")
    while True:
        try:
            text = c.input("[bold cyan]You:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/new":
            brain.reset("chat")
            continue
        c.print("[bold yellow]Jarvis:[/] ", end="")

        def on_text(chunk):
            c.print(chunk, end="", markup=False, highlight=False)
            if speaker:
                speaker.feed(chunk)

        brain.ask(text, session="chat", channel="terminal" if not speaker else "voice",
                  on_text=on_text, on_status=lambda n: c.print(f"[dim]⚙ {n}[/] ", end=""))
        c.print()
        if speaker:
            speaker.flush()
    if speaker:
        speaker.wait(30)


def cmd_voice(args, with_scheduler=True):
    from .brain import get_brain
    from .voice.assistant import VoiceAssistant
    c = _console()
    _require_key(c)
    if with_scheduler:
        from .scheduler import Scheduler
        Scheduler(get_brain).start()
    with c.status("Loading voice models…"):
        va = VoiceAssistant(get_brain(), push_to_talk=args.ptt, console=c)
    try:
        va.run()
    except KeyboardInterrupt:
        c.print("\nGoodbye.")


# ---------------------------------------------------------------- server / telegram / run
def _serve(block=True, https=True):
    import uvicorn
    from .server.app import app
    s = config.get()["server"]
    kw = {}
    cert, key = config.DATA_DIR / "cert.pem", config.DATA_DIR / "key.pem"
    if https and cert.exists():
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    server = uvicorn.Server(uvicorn.Config(app, host=s["host"], port=s["port"], log_level="warning", **kw))
    if block:
        server.run()
    else:
        threading.Thread(target=server.run, daemon=True, name="jarvis-server").start()
    return server


def cmd_serve(args):
    from .scheduler import Scheduler
    from .brain import get_brain
    c = _console()
    _require_key(c)
    Scheduler(get_brain).start()
    _print_phone_help(c)
    _serve(block=True, https=not args.http)


def cmd_telegram(args):
    from .telegram_bot import run_bot
    run_bot()


def cmd_run(args):
    from .brain import get_brain
    from .scheduler import Scheduler
    c = _console()
    _require_key(c)
    Scheduler(get_brain).start()
    _serve(block=False)
    c.print(f"[green]✓ phone/web server on port {config.get()['server']['port']}")
    from . import tg_user
    if tg_user.is_configured():
        tg = tg_user.get()
        c.print("[green]✓ Telegram account linked" if not tg._error else f"[yellow]! {tg._error}")
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        from .telegram_bot import run_bot
        threading.Thread(target=run_bot, daemon=True, name="jarvis-telegram").start()
        c.print("[green]✓ Telegram bot")
    if args.no_voice:
        c.print("Running headless (no microphone). Ctrl+C to quit.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            return
    else:
        cmd_voice(args, with_scheduler=False)


# ---------------------------------------------------------------- voices
def cmd_voices(args):
    from .voice.tts import Speaker, Voice
    c = _console()
    if args.set:
        name, _, spk = args.set.partition(":")
        if name not in {v.split(":")[0] for v in VOICE_CHOICES} and not (config.MODELS_DIR / "voices" / f"{name}.onnx").exists():
            c.print(f"[yellow]Unknown voice {name}; trying to download it from the Piper catalogue…")
        download_voice(name)
        config.set_value("voice.piper_voice", name)
        config.set_value("voice.piper_speaker", int(spk) if spk else None)
        config.set_value("voice.tts_engine", "piper")
        c.print(f"[green]Jarvis now speaks with {args.set}.")
        Speaker().say("Very good. This is my new voice. I hope it suits.")
        import time
        time.sleep(0.5)
        return
    line = args.text or "Good evening. I have prepared a safety briefing for you to entirely ignore."
    for key, desc in VOICE_CHOICES.items():
        name, _, spk = key.partition(":")
        if not (config.MODELS_DIR / "voices" / f"{name}.onnx").exists():
            download_voice(name)
        c.print(f"[bold cyan]{key}[/] — {desc}")
        config.get()["voice"].update({"piper_voice": name, "piper_speaker": int(spk) if spk else None,
                                      "tts_engine": "piper"})
        sp = Speaker(Voice())
        sp.say(line)
        sp.wait(60)
    config.reload()
    c.print("\nPick one with: [bold]jarvis voices --set en_GB-alan-medium[/] "
            "(use name:N for semaine speakers, e.g. en_GB-semaine-medium:2)")


def cmd_say(args):
    from .voice.tts import Speaker
    sp = Speaker()
    sp.say(" ".join(args.text))
    sp.wait(120)


# ---------------------------------------------------------------- telegram account / phone app
def cmd_telegram_login(args):
    from . import tg_user
    if tg_user.credentials() is None and sys.stdin.isatty():
        print("Create an app at https://my.telegram.org → API development tools (any name works).")
        api_id = input("api_id: ").strip()
        api_hash = input("api_hash: ").strip()
        if api_id.isdigit() and api_hash:
            config.set_env("TELEGRAM_API_ID", api_id)
            config.set_env("TELEGRAM_API_HASH", api_hash)
    tg_user.login()


ANDROID_DIR = config.ROOT / "android"
TOOLCHAIN = Path.home() / ".jarvis-toolchain"


def cmd_build_phone(args):
    env = dict(os.environ, JAVA_HOME=str(TOOLCHAIN / "jdk"), ANDROID_HOME=str(TOOLCHAIN / "android-sdk"))
    gradle = TOOLCHAIN / "gradle" / "bin" / "gradle"
    if not gradle.exists():
        raise SystemExit("Android toolchain missing; run scripts/fetch-android-toolchain.sh first.")
    r = subprocess.run([str(gradle), "--no-daemon", "-q", "assembleDebug"], cwd=ANDROID_DIR, env=env)
    if r.returncode != 0:
        raise SystemExit("Build failed.")
    apk = ANDROID_DIR / "app/build/outputs/apk/debug/app-debug.apk"
    import shutil
    shutil.copy(apk, config.DATA_DIR / "jarvis-phone.apk")
    print(f"Built {config.DATA_DIR / 'jarvis-phone.apk'} ({apk.stat().st_size // 1024} KB).")
    print("On the phone, open the setup page from `jarvis phone-setup`, or install over USB: adb install -r data/jarvis-phone.apk")


def cmd_phone_setup(args):
    from dotenv import load_dotenv
    load_dotenv(config.ENV_PATH, override=True)
    port = config.get()["server"]["port"]
    print("With `jarvis run` (or `jarvis serve`) running, open this on your phone (same Wi-Fi):")
    print(f"  https://{lan_ip()}:{port}/phone-setup?token={os.environ.get('JARVIS_WEB_TOKEN', '')}")
    print("It lets you download Jarvis Phone and link it to this Mac.")


# ---------------------------------------------------------------- launchd
PLIST = Path.home() / "Library/LaunchAgents/com.jarvis.assistant.plist"


def cmd_install_service(args):
    exe = Path(sys.executable).parent / "jarvis"
    mode = ["run", "--no-voice"] if args.no_voice else ["run"]
    PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jarvis.assistant</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-s</string>
    <string>{exe}</string>{''.join(f'<string>{m}</string>' for m in mode)}
  </array>
  <key>WorkingDirectory</key><string>{config.ROOT}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{config.DATA_DIR}/service.out.log</string>
  <key>StandardErrorPath</key><string>{config.DATA_DIR}/service.err.log</string>
</dict></plist>
""")
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    print(f"Installed {PLIST}. Jarvis now starts at login (logs in {config.DATA_DIR}).")
    print("Note: the first run may trigger macOS microphone/automation permission prompts.")


def cmd_uninstall_service(args):
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print("Removed the Jarvis login service.")


# ---------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(prog="jarvis", description="Your personal assistant.")
    p.add_argument("-v", "--verbose", action="store_true", help="log to the terminal")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("setup"); s.add_argument("--minimal", action="store_true", help="only the default voice"); s.set_defaults(fn=cmd_setup)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("chat"); s.add_argument("--speak", action="store_true"); s.set_defaults(fn=cmd_chat)
    s = sub.add_parser("voice"); s.add_argument("--ptt", action="store_true", help="push-to-talk with Enter"); s.set_defaults(fn=cmd_voice)
    s = sub.add_parser("serve"); s.add_argument("--http", action="store_true"); s.set_defaults(fn=cmd_serve)
    sub.add_parser("telegram").set_defaults(fn=cmd_telegram)
    s = sub.add_parser("run"); s.add_argument("--no-voice", action="store_true"); s.add_argument("--ptt", action="store_true"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("voices"); s.add_argument("--set"); s.add_argument("--text"); s.set_defaults(fn=cmd_voices)
    s = sub.add_parser("say"); s.add_argument("text", nargs="+"); s.set_defaults(fn=cmd_say)
    sub.add_parser("telegram-login", help="link your own Telegram account").set_defaults(fn=cmd_telegram_login)
    sub.add_parser("build-phone", help="build the Android app").set_defaults(fn=cmd_build_phone)
    sub.add_parser("phone-setup", help="show the phone install/link page").set_defaults(fn=cmd_phone_setup)
    s = sub.add_parser("install-service"); s.add_argument("--no-voice", action="store_true"); s.set_defaults(fn=cmd_install_service)
    sub.add_parser("uninstall-service").set_defaults(fn=cmd_uninstall_service)
    args = p.parse_args(argv)
    config.get()
    _log_setup(args.verbose)
    if not getattr(args, "fn", None):
        p.print_help()
        return
    args.fn(args)


if __name__ == "__main__":
    main()
