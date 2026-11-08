"""Mac system controls: Wi-Fi, Bluetooth, brightness, dark mode, power, Shortcuts,
Spotlight search, clipboard, screenshots. No admin rights needed.

Bluetooth and brightness use tiny native helpers in native/bin (built by native/build.sh)."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from anthropic import beta_tool

from .. import config
from .macos import _safe, osascript

RISK = {"sleep_mac": "confirm", "restart_or_shutdown": "confirm", "empty_trash": "confirm"}

IS_MAC = sys.platform == "darwin"
NATIVE = config.ROOT / "native" / "bin"


def _run(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _native(name: str) -> Path | None:
    p = NATIVE / name
    if p.exists():
        return p
    build = config.ROOT / "native" / "build.sh"
    if build.exists():
        _run(["bash", str(build)], timeout=120)
    return p if p.exists() else None


def wifi_device() -> str:
    out = _run(["networksetup", "-listallhardwareports"]).stdout
    m = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\nDevice: (\w+)", out)
    return m.group(1) if m else "en0"


# ---------------------------------------------------------------- radios
@beta_tool(eager_input_streaming=True)
def wifi(action: str = "status") -> str:
    """Turn the Mac's Wi-Fi on or off, or report its status and network name.

    Args:
        action: "on", "off" or "status".
    """
    dev = wifi_device()
    if action in ("on", "off"):
        r = _run(["networksetup", "-setairportpower", dev, action])
        if r.returncode != 0:
            return f"Error: couldn't turn Wi-Fi {action} ({r.stderr.strip() or r.stdout.strip()})."
        return f"Wi-Fi is {action}."
    power = _run(["networksetup", "-getairportpower", dev]).stdout.strip().rsplit(" ", 1)[-1].lower()
    if power != "on":
        return "Wi-Fi is off."
    net = _run(["networksetup", "-getairportnetwork", dev]).stdout.strip()
    name = net.split(": ", 1)[1] if ": " in net and "not associated" not in net else None
    return f"Wi-Fi is on, connected to {name}." if name else "Wi-Fi is on but not connected."


@beta_tool(eager_input_streaming=True)
def bluetooth(action: str = "status") -> str:
    """Turn the Mac's Bluetooth on or off, or report whether it's on.

    Args:
        action: "on", "off" or "status".
    """
    exe = _native("jarvis-bluetooth")
    if exe is None:
        return "Error: Bluetooth helper missing (run native/build.sh)."
    arg = action if action in ("on", "off") else "status"
    r = _run([str(exe), arg])
    state = r.stdout.strip() or "unknown"
    if arg != "status" and r.returncode != 0:
        return f"Error: Bluetooth is still {state}."
    return f"Bluetooth is {state}."


# ---------------------------------------------------------------- display
@beta_tool(eager_input_streaming=True)
def brightness(level: int = -1, change: int = 0) -> str:
    """Set or adjust the screen brightness (built-in display).

    Args:
        level: Absolute brightness 0-100. Leave -1 to keep the current level.
        change: Relative change, e.g. 20 (brighter) or -20 (dimmer). Ignored when level is set.
    """
    exe = _native("jarvis-brightness")
    if exe is None:
        return "Error: brightness helper missing (run native/build.sh)."
    cur = _run([str(exe)]).stdout.strip()
    if not cur.isdigit():
        return "Error: couldn't read the screen brightness."
    target = level if level >= 0 else int(cur) + change if change else None
    if target is None:
        return f"Brightness is {cur} percent."
    target = max(0, min(100, target))
    r = _run([str(exe), str(target)])
    return f"Brightness set to {r.stdout.strip() or target} percent." if r.returncode == 0 else "Error: couldn't set brightness."


@beta_tool(eager_input_streaming=True)
def dark_mode(action: str = "toggle") -> str:
    """Switch the Mac between dark and light appearance.

    Args:
        action: "on" (dark), "off" (light), "toggle" or "status".
    """
    def is_dark() -> bool:  # no Automation permission needed to read it
        return _run(["defaults", "read", "-g", "AppleInterfaceStyle"]).stdout.strip() == "Dark"

    if action == "status":
        return "Dark mode is " + ("on." if is_dark() else "off.")
    want = {"on": True, "off": False}.get(action, not is_dark())
    res = _safe(lambda: osascript('tell application "System Events" to tell appearance preferences to '
                                  f'set dark mode to {"true" if want else "false"}', timeout=30))
    if res.startswith("Error"):
        return res + " (macOS may be asking you to allow Jarvis to control System Events.)"
    return "Dark mode is " + ("on." if want else "off.")


@beta_tool(eager_input_streaming=True)
def volume(action: str = "status", amount: int = 10) -> str:
    """Adjust or report the Mac's sound: up, down, mute, unmute or status. (Use set_volume for an exact level.)

    Args:
        action: "up", "down", "mute", "unmute" or "status".
        amount: Step size in percent for up/down.
    """
    get = "output volume of (get volume settings)"
    try:
        if action == "mute":
            osascript("set volume with output muted")
            return "Muted."
        if action == "unmute":
            osascript("set volume without output muted")
            return f"Unmuted, volume {osascript(get)} percent."
        cur = int(osascript(get) or 0)
        if action in ("up", "down"):
            new = max(0, min(100, cur + (amount if action == "up" else -amount)))
            osascript(f"set volume output volume {new} without output muted")
            return f"Volume {new} percent."
        muted = osascript("output muted of (get volume settings)") == "true"
        return f"Volume is {cur} percent" + (", muted." if muted else ".")
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        return f"Error: {e}"


# ---------------------------------------------------------------- power
@beta_tool(eager_input_streaming=True)
def keep_awake(minutes: int = 60) -> str:
    """Stop the Mac and its display from sleeping for a while (e.g. during a download or presentation).

    Args:
        minutes: How long to stay awake.
    """
    minutes = max(1, min(int(minutes), 24 * 60))
    subprocess.Popen(["caffeinate", "-d", "-i", "-t", str(minutes * 60)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"I'll keep the Mac awake for {minutes} minutes."


@beta_tool(eager_input_streaming=True)
def sleep_mac(confirmed: bool = False) -> str:
    """Put the Mac to sleep (Jarvis stops listening until it wakes). Confirm first.

    Args:
        confirmed: True only after the user said yes.
    """
    if not confirmed:
        return "CONFIRMATION NEEDED: ask the user whether to put the Mac to sleep now."
    subprocess.Popen(["bash", "-c", "sleep 3; pmset sleepnow"])
    return "Going to sleep in three seconds."


@beta_tool(eager_input_streaming=True)
def restart_or_shutdown(action: str, confirmed: bool = False) -> str:
    """Restart or shut down the Mac. Apps get the chance to save. Always confirm first.

    Args:
        action: "restart" or "shutdown".
        confirmed: True only after the user explicitly said yes.
    """
    if action not in ("restart", "shutdown"):
        return "Error: action must be restart or shutdown."
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user to confirm they want to {action} the Mac now."
    verb = "restart" if action == "restart" else "shut down"
    subprocess.Popen(["bash", "-c", f"sleep 4; osascript -e 'tell application \"System Events\" to {verb}'"])
    return f"Very well. I'll {verb} the Mac in a few seconds."


@beta_tool(eager_input_streaming=True)
def empty_trash(confirmed: bool = False) -> str:
    """Empty the Finder Trash permanently. Always confirm first.

    Args:
        confirmed: True only after the user explicitly said yes.
    """
    if not confirmed:
        return "CONFIRMATION NEEDED: ask the user to confirm permanently emptying the Trash."
    return _safe(lambda: (osascript('tell application "Finder" to empty trash', timeout=60), "Trash emptied.")[1])


# ---------------------------------------------------------------- utilities
@beta_tool(eager_input_streaming=True)
def take_screenshot() -> str:
    """Take a screenshot of the whole screen and save it to the Desktop."""
    path = Path.home() / "Desktop" / f"Jarvis screenshot {datetime.now():%Y-%m-%d at %H.%M.%S}.png"
    r = _run(["screencapture", "-x", str(path)])
    return f"Screenshot saved to your Desktop as {path.name}." if r.returncode == 0 and path.exists() \
        else "Error: screenshot failed (allow Screen Recording for your terminal in System Preferences)."


@beta_tool(eager_input_streaming=True)
def run_shortcut(name: str, input_text: str = "") -> str:
    """Run one of the user's macOS Shortcuts by name (e.g. a Focus/Do Not Disturb toggle,
    a HomeKit scene, or any custom automation). Use list_shortcuts to see what exists.

    Args:
        name: Shortcut name exactly as in the Shortcuts app (case-insensitive match is attempted).
        input_text: Optional text passed to the shortcut as input.
    """
    names = [n for n in _run(["shortcuts", "list"]).stdout.splitlines() if n.strip()]
    match = next((n for n in names if n.lower() == name.lower()), None) or \
        next((n for n in names if name.lower() in n.lower()), None)
    if not match:
        return f"No shortcut called '{name}'. " + (f"Available: {', '.join(names[:20])}." if names else
                                                    "The user has no shortcuts yet; they can make one in the Shortcuts app.")
    args = ["shortcuts", "run", match]
    if input_text:
        r = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=60)
    else:
        r = _run(args, timeout=60)
    out = r.stdout.strip()
    return f"Ran '{match}'." + (f" Output: {out[:500]}" if out else "") if r.returncode == 0 \
        else f"Error running '{match}': {r.stderr.strip()[:200]}"


@beta_tool(eager_input_streaming=True)
def list_shortcuts() -> str:
    """List the user's macOS Shortcuts."""
    names = [n for n in _run(["shortcuts", "list"]).stdout.splitlines() if n.strip()]
    return ", ".join(names) if names else "No shortcuts yet (they can be made in the Shortcuts app)."


@beta_tool(eager_input_streaming=True)
def find_files(query: str, open_first: bool = False) -> str:
    """Find files on the Mac with Spotlight by name or content, optionally opening the best match.

    Args:
        query: What to look for, e.g. "resume pdf" or "tax 2025".
        open_first: Open the top result.
    """
    words = [w for w in re.split(r"\s+", query.strip()) if w]
    if not words:
        return "Error: empty search."
    name_q = " && ".join(f'kMDItemFSName == "*{w}*"cd' for w in words)
    r = _run(["mdfind", "-onlyin", str(Path.home()), name_q], timeout=20)
    hits = [h for h in r.stdout.splitlines() if "/Library/" not in h and "/." not in h]
    if not hits:
        r = _run(["mdfind", "-onlyin", str(Path.home()), query], timeout=20)
        hits = [h for h in r.stdout.splitlines() if "/Library/" not in h and "/." not in h]
    if not hits:
        return f"Nothing found for '{query}'."
    hits.sort(key=lambda h: -Path(h).stat().st_mtime if Path(h).exists() else 0)
    if open_first:
        subprocess.run(["open", hits[0]])
    home = str(Path.home())
    listing = "\n".join(h.replace(home, "~") for h in hits[:8])
    return (f"Opened {Path(hits[0]).name}.\n" if open_first else "") + f"Found {len(hits)}:\n{listing}"


@beta_tool(eager_input_streaming=True)
def open_path(path: str) -> str:
    """Open a file or folder on the Mac (e.g. "~/Downloads") in its default app or Finder.

    Args:
        path: File or folder path; ~ is allowed.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: {path} doesn't exist."
    subprocess.run(["open", str(p)])
    return f"Opened {p.name or str(p)}."


@beta_tool(eager_input_streaming=True)
def clipboard(text: str = "") -> str:
    """Read the clipboard (no text given) or copy text to it.

    Args:
        text: Text to copy. Empty = read the current clipboard.
    """
    if text:
        subprocess.run(["pbcopy"], input=text, text=True)
        return "Copied to the clipboard."
    content = _run(["pbpaste"]).stdout
    return f"Clipboard: {content[:1500]}" if content.strip() else "The clipboard is empty."


SETTINGS_PANES = {
    "wifi": "com.apple.preference.network", "network": "com.apple.preference.network",
    "bluetooth": "com.apple.preferences.Bluetooth", "sound": "com.apple.preference.sound",
    "display": "com.apple.preference.displays", "displays": "com.apple.preference.displays",
    "battery": "com.apple.preference.battery", "privacy": "com.apple.preference.security",
    "security": "com.apple.preference.security", "notifications": "com.apple.preference.notifications",
    "keyboard": "com.apple.preference.keyboard", "general": "com.apple.preference.general",
    "users": "com.apple.preferences.users", "updates": "com.apple.preferences.softwareupdate",
    "focus": "com.apple.preference.notifications", "dock": "com.apple.preference.dock",
}


@beta_tool(eager_input_streaming=True)
def open_settings(pane: str = "") -> str:
    """Open System Preferences, optionally at a pane: wifi, bluetooth, sound, displays, battery,
    privacy, notifications, keyboard, general, users, updates, dock.

    Args:
        pane: Pane name; empty opens the main window.
    """
    key = SETTINGS_PANES.get(pane.lower().replace("-", "").replace(" ", ""))
    url = f"x-apple.systempreferences:{key}" if key else None
    subprocess.run(["open", url] if url else ["open", "-a", "System Preferences"])
    return f"Opened {pane} settings." if url else "Opened System Preferences."


TOOLS = [wifi, bluetooth, brightness, dark_mode, volume, keep_awake, sleep_mac, restart_or_shutdown,
         empty_trash, take_screenshot, run_shortcut, list_shortcuts, find_files, open_path, clipboard,
         open_settings] if IS_MAC else []
