"""Open Claude sessions: Claude Code in Terminal, the Claude desktop app, or claude.ai."""
from __future__ import annotations

import difflib
import shlex
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

from anthropic import beta_tool

from .macos import IS_MAC, _q, _safe, osascript

RISK: dict = {}

PROJECTS_DIR = Path.home() / "Documents" / "GitHub"


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def _resolve_folder(folder: str) -> Path | None:
    if not folder:
        return PROJECTS_DIR if PROJECTS_DIR.exists() else Path.home()
    p = Path(folder).expanduser()
    if p.is_absolute() and p.is_dir():  # bare names are project names, never cwd-relative
        return p
    if PROJECTS_DIR.exists():
        names = {d.name: d for d in PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")}
        lower = {n.lower(): d for n, d in names.items()}
        if folder.lower() in lower:
            return lower[folder.lower()]
        match = difflib.get_close_matches(folder.lower(), list(lower), n=1, cutoff=0.5)
        if match:
            return lower[match[0]]
    return None


@beta_tool(eager_input_streaming=True)
def list_projects() -> str:
    """List the user's code projects (folders in ~/Documents/GitHub)."""
    if not PROJECTS_DIR.exists():
        return "No ~/Documents/GitHub folder."
    names = sorted(d.name for d in PROJECTS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
    return ", ".join(names) or "No projects."


@beta_tool(eager_input_streaming=True)
def open_claude(where: str = "code", project: str = "", prompt: str = "") -> str:
    """Open a Claude session. where="code" starts Claude Code in a new Terminal window
    (in a project folder, optionally with a first prompt); "desktop" opens the Claude app;
    "web" opens claude.ai in the browser (optionally with the prompt pre-filled).

    Args:
        where: "code", "desktop" or "web".
        project: Project folder name in ~/Documents/GitHub (fuzzy) or a path. Code sessions only.
        prompt: Optional first message for Claude.
    """
    if not IS_MAC:
        return "Error: macOS only."
    if where == "desktop":
        r = subprocess.run(["open", "-a", "Claude"], capture_output=True)
        return "Opened the Claude app." if r.returncode == 0 else "Error: Claude app not found."
    if where == "web":
        url = "https://claude.ai/new"
        if prompt:
            url += "?q=" + urllib.parse.quote(prompt)
        subprocess.run(["open", url])
        return "Opened Claude in the browser."
    folder = _resolve_folder(project)
    if folder is None:
        return f"Error: no project matching '{project}'. Known projects: {list_projects.call({})}"
    cmd = f"cd {shlex.quote(str(folder))} && {shlex.quote(_claude_bin())}"
    if prompt:
        cmd += " " + shlex.quote(prompt)
    script = f'tell application "Terminal"\n  activate\n  do script {_q(cmd)}\nend tell'
    res = _safe(lambda: osascript(script))
    if res.startswith("Error"):
        return res
    return f"Started Claude Code in {folder.name}" + (" with your prompt." if prompt else ".")


TOOLS = [list_projects, open_claude] if sys.platform == "darwin" else []
