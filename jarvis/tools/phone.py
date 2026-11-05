"""Send things to the user's phone (ntfy push notifications)."""
from __future__ import annotations

from anthropic import beta_tool

from .. import bus

RISK: dict = {}


@beta_tool(eager_input_streaming=True)
def send_to_phone(message: str, title: str = "Jarvis") -> str:
    """Push a notification to the user's Android phone (e.g. a link, address, list or
    something to read later).

    Args:
        message: Notification text.
        title: Short notification title.
    """
    if bus.push_to_phone(message, title=title):
        return "Sent to your phone."
    return "Error: phone notifications aren't set up (run `jarvis setup` and install the ntfy app)."


TOOLS = [send_to_phone]
