# Contributing to Jarvis

Thanks for helping! Ideas, bug reports, and code are all welcome.

## Ways to help
- **Suggest something:** open an issue with the *Feature idea* template. Say what you'd say to Jarvis and what should happen.
- **Report a bug:** use the *Bug report* template. Include your OS and hardware, Android version if it's the phone app, and the relevant lines from `data/jarvis.log`.
- **Send code:** fork, create a branch, and open a pull request. Small, focused PRs get merged fastest.

## Dev setup
```bash
python3.12 -m venv .venv && source .venv/bin/activate   # or conda, see README
pip install -e ".[dev]"
pytest -q                     # runs without an API key or microphone
jarvis setup && jarvis chat   # try it (needs an Anthropic API key)
```
Android app: `jarvis build-phone` (downloads JDK 17 + Android SDK into `~/.jarvis-toolchain` on first run;
the download script is `scripts/fetch-android-toolchain.sh`), or open `android/` in Android Studio.

## Adding a capability (a "tool")
Tools live in `jarvis/tools/`, one module per area. The checklist is in
[`.claude/skills/jarvis-add-tool/SKILL.md`](.claude/skills/jarvis-add-tool/SKILL.md). The short version:
1. Write a typed function with `@beta_tool(eager_input_streaming=True)`. Its docstring is what Claude sees.
2. Return short, speakable strings, and return errors as strings instead of raising.
3. Anything that sends, deletes, calls or quits needs a `confirmed: bool = False` argument and must return `CONFIRMATION NEEDED: …` until the user says yes.
4. Add it to the module's `TOOLS` list and write a test.

## Ground rules
- History in `jarvis/brain.py` is append-only and the system prompt and tool list stay byte-stable (prompt caching). Put per-turn info in the user message.
- Keep it light: Jarvis targets old hardware (2015 Intel MacBook, CPU only). Avoid heavy dependencies.
- Never commit secrets, `data/` or `models/`. Use `.env`.
- Be kind in issues and reviews.
