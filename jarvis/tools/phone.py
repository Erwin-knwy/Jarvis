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




# ---------------------------------------------------------------- Jarvis Phone app (Android)
def _phone(cmd: dict, timeout: float = 20) -> str:
    from ..phone_link import link
    try:
        r = link.request(cmd, timeout=timeout)
    except (ConnectionError, TimeoutError) as e:
        return f"Error: {e or 'the phone did not answer in time'}"
    except Exception as e:  # concurrent.futures.TimeoutError etc.
        return f"Error: phone request failed ({type(e).__name__})."
    return str(r.get("result")) if r.get("ok") else f"Error: {r.get('error', 'unknown phone error')}"


@beta_tool(eager_input_streaming=True)
def phone_send_sms(contact: str, text: str, confirmed: bool = False) -> str:
    """Send an SMS from the user's Android phone to a contact (name as saved in the phone)
    or a number. Read back the recipient and text and get a clear yes first.

    Args:
        contact: Contact name as saved on the phone, or a phone number.
        text: The SMS text.
        confirmed: True only after the user approved the recipient and exact text.
    """
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user to confirm texting {contact}: \"{text}\""
    return _phone({"cmd": "send_sms", "contact": contact, "text": text})


@beta_tool(eager_input_streaming=True)
def phone_call(contact: str, confirmed: bool = False) -> str:
    """Start a phone call on the user's Android phone. Confirm with the user first.

    Args:
        contact: Contact name as saved on the phone, or a phone number.
        confirmed: True only after the user said yes.
    """
    if not confirmed:
        return f"CONFIRMATION NEEDED: ask the user to confirm calling {contact}."
    return _phone({"cmd": "call", "contact": contact})


@beta_tool(eager_input_streaming=True)
def phone_recent_sms(contact: str = "", limit: int = 5) -> str:
    """Read recent SMS messages received on the phone, optionally only from one contact.

    Args:
        contact: Optional contact name or number to filter by.
        limit: Number of messages.
    """
    return _phone({"cmd": "recent_sms", "contact": contact, "limit": max(1, min(limit, 20))})


@beta_tool(eager_input_streaming=True)
def phone_status() -> str:
    """Whether the phone app is connected, plus the phone's battery level."""
    from ..phone_link import link
    if not link.connected:
        return "The phone app is not connected."
    return _phone({"cmd": "status"}, timeout=8)


TOOLS = [send_to_phone, phone_send_sms, phone_call, phone_recent_sms, phone_status]
RISK = {"phone_send_sms": "confirm", "phone_call": "confirm"}
