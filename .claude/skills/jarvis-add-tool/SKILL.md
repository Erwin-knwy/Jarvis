---
name: jarvis-add-tool
description: Checklist for adding a new tool/capability to Jarvis (jarvis/tools/). Use when the user asks Jarvis to "be able to", "learn to", or "control" something new, or when adding any @beta_tool function.
---

# Adding a tool to Jarvis

Jarvis's brain is Claude driven by the Anthropic SDK tool runner (`client.beta.messages.tool_runner`). Every capability is a typed Python function decorated with `@beta_tool`. For SDK details, load the `claude-api` skill first and do not guess API shapes.

## Steps

1. **Pick the module** in `jarvis/tools/` by domain (`basics.py`, `notes.py`, `macos.py`, ...). Create a new module only for a new domain.
2. **Write the function**:
   ```python
   from anthropic import beta_tool

   @beta_tool
   def set_volume(level: int) -> str:
       """Set the Mac's output volume.

       Args:
           level: Volume from 0 to 100.
       """
   ```
   - The docstring is the tool description Claude sees. Say *when* to use it, in one or two sentences.
   - Type every argument and document it under `Args:`.
   - Return a short string (or JSON string) that is easy to read aloud. Never raise for expected failures. Return a clear error message instead.
   - Keep it fast (< 1 s). For slow work, start a background task and return immediately.
   - Decorate with `@beta_tool(eager_input_streaming=True)` like the existing tools. `tool.call()` validates inputs.
3. **Assign a risk level** in the module's `RISK` dict. Anything that deletes, sends, buys, or quits apps is `"confirm"`: add a `confirmed: bool = False` arg and return `"CONFIRMATION NEEDED: ask the user …"` until it's true (see `notes.delete_note`). The system prompt already tells Claude how to handle this.
4. **Register it**: append it to the module's `TOOLS` list. For a new module, add it to `_MODULES` in `jarvis/tools/__init__.py` (the registry sorts by name automatically, which keeps the prompt cache valid).
5. **Platform guards**: macOS‑only tools check `sys.platform == "darwin"`. Network tools set timeouts (≤ 5 s).
6. **Test** in `tests/test_core.py` (or a new `tests/test_<module>.py`): mock subprocess/HTTP and assert the returned string.
7. **Run** `./.venv/bin/python -m pytest -q`, then **try it**: `jarvis chat` → ask for it in natural language → confirm Claude picks the tool.

## Don'ts

- No generic `run_shell(cmd)` tool. Expose specific, allowlisted actions.
- Don't put dynamic data (time, user name lookups) in the system prompt. Expose it as a tool.
- No secrets in tool return values.
