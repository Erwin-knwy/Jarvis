"""Read and reply to your own Telegram chats (as you, not as the bot)."""
from __future__ import annotations

from anthropic import beta_tool

from .. import tg_user

RISK = {"telegram_send_message": "confirm"}


def _fmt_time(dt) -> str:
    return dt.astimezone().strftime("%a %H:%M") if dt else ""


@beta_tool(eager_input_streaming=True)
def telegram_messages(chat: str = "", limit: int = 8) -> str:
    """Read Telegram. With no chat: list chats that have unread messages. With a chat
    (person/group name or @username): the latest messages in that chat.

    Args:
        chat: Contact or group name, e.g. "Mom" or "@alex". Empty = overview of unread chats.
        limit: How many messages to return for a chat.
    """
    tg = tg_user.get()

    async def overview(client):
        dialogs = await client.get_dialogs(limit=100)  # fresh unread counts
        unread = [d for d in dialogs if d.unread_count > 0 and not d.archived]
        if not unread:
            return "No unread Telegram messages."
        lines = []
        for d in unread[:10]:
            last = d.message.message if d.message and d.message.message else "(media)"
            lines.append(f"{d.name}: {d.unread_count} unread — last: {last[:150]}")
        return "\n".join(lines)

    async def read_chat(client):
        d = await tg.find_chat(client, chat)
        if d is None:
            return f"No Telegram chat matching '{chat}'."
        msgs = await client.get_messages(d.entity, limit=max(1, min(limit, 30)))
        lines = []
        for m in reversed(msgs):
            who = "You" if m.out else tg_user._name(await m.get_sender())
            lines.append(f"[{_fmt_time(m.date)}] {who}: {m.message or '(media)'}")
        return f"Chat with {d.name}:\n" + "\n".join(lines)

    try:
        return tg.run(read_chat if chat else overview)
    except Exception as e:
        return f"Error: {e}"


@beta_tool(eager_input_streaming=True)
def telegram_send_message(chat: str, text: str, confirmed: bool = False) -> str:
    """Send a Telegram message as the user. First read the message back and ask the user
    to confirm; call again with confirmed=true only after a clear yes.

    Args:
        chat: Contact or group name, or @username.
        text: The message to send, exactly as it should appear.
        confirmed: True only after the user approved the exact text and recipient.
    """
    tg = tg_user.get()

    async def send(client):
        d = await tg.find_chat(client, chat)
        if d is None:
            return f"No Telegram chat matching '{chat}'."
        if not confirmed:
            return f"CONFIRMATION NEEDED: ask the user to confirm sending to {d.name}: \"{text}\""
        await client.send_message(d.entity, text)
        await client.send_read_acknowledge(d.entity)
        return f"Sent to {d.name} on Telegram."

    try:
        return tg.run(send)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [telegram_messages, telegram_send_message]
