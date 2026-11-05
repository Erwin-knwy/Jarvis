"""Telegram front-end: chat with Jarvis by text or voice note from anywhere.

Set TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_ALLOWED_USERS (your numeric
user id; the bot tells you yours if you message it while unlisted)."""
from __future__ import annotations

import asyncio
import io
import logging
import os

import numpy as np

from . import bus
from .brain import get_brain

log = logging.getLogger(__name__)


def allowed_users() -> set[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def wav_to_ogg_opus(audio: np.ndarray, sr: int) -> bytes:
    """Encode int16 mono audio as OGG/Opus (Telegram voice-note format)."""
    import av

    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sr
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        for f in resampler.resample(frame) + resampler.resample(None):
            for p in stream.encode(f):
                out.mux(p)
        for p in stream.encode(None):
            out.mux(p)
    return buf.getvalue()


def run_bot(token: str | None = None) -> None:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    asyncio.set_event_loop(asyncio.new_event_loop())
    voice = None

    def get_voice():
        nonlocal voice
        if voice is None:
            from .voice.tts import Voice
            voice = Voice()
        return voice

    async def guard(update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else None
        if uid in allowed_users():
            return True
        await update.effective_message.reply_text(
            f"Sorry, I only work for my owner. Your Telegram user id is {uid}. "
            "Add it to TELEGRAM_ALLOWED_USERS in Jarvis's .env to get access."
        )
        return False

    async def answer(update: Update, text: str, with_voice: bool):
        uid = update.effective_user.id
        await update.effective_chat.send_action("typing")
        reply = await asyncio.to_thread(get_brain().ask, text, session=f"telegram:{uid}", channel="telegram")
        await update.effective_message.reply_text(reply or "…")
        if with_voice and reply:
            try:
                audio, sr = await asyncio.to_thread(get_voice().synth, reply)
                ogg = await asyncio.to_thread(wav_to_ogg_opus, audio, sr)
                await update.effective_message.reply_voice(ogg)
            except Exception:
                log.exception("voice reply failed")

    async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await update.effective_message.reply_text("At your service. Send text or a voice note.")

    async def on_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            get_brain().reset(f"telegram:{update.effective_user.id}")
            await update.effective_message.reply_text("Fresh conversation.")

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await answer(update, update.effective_message.text, with_voice=False)

    async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        from .voice.stt import shared

        msg = update.effective_message
        f = await (msg.voice or msg.audio).get_file()
        data = bytes(await f.download_as_bytearray())
        text = await asyncio.to_thread(shared().transcribe_bytes, data)
        if not text:
            await msg.reply_text("I couldn't make that out, sorry.")
            return
        await msg.reply_text(f"🎙 {text}")
        await answer(update, text, with_voice=True)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("new", on_new))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    loop = asyncio.get_event_loop()

    def on_announce(text: str):
        for uid in allowed_users():
            asyncio.run_coroutine_threadsafe(app.bot.send_message(uid, f"🔔 {text}"), loop)

    bus.subscribe(on_announce)
    log.info("telegram bot polling")
    app.run_polling(stop_signals=None, close_loop=False)
