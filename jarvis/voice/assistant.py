"""The voice loop: wake word → listen (VAD) → transcribe → brain → speak.

One thread reads the microphone continuously and runs a small state machine, so
the wake word keeps working while Jarvis is talking (say "Hey Jarvis" to interrupt).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from enum import Enum

import numpy as np

from .. import bus, config
from .stt import Transcriber
from .tts import Speaker, beep

log = logging.getLogger(__name__)

SR = 16000
FRAME = 1280  # 80 ms, what openWakeWord expects


class State(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    BUSY = "busy"


class VoiceAssistant:
    def __init__(self, brain, speaker: Speaker | None = None, push_to_talk: bool = False,
                 console=None):
        v = config.get()["voice"]
        self.v = v
        self.brain = brain
        self.console = console
        self.speaker = speaker or Speaker()
        self.stt = Transcriber()
        self.push_to_talk = push_to_talk
        self._mic_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.state = State.IDLE
        self._turn_id = 0
        self._busy_done = threading.Event()
        self._ptt = threading.Event()
        self._load_models()

    # ----------------------------------------------------------------- setup
    def _load_models(self):
        from openwakeword.model import Model
        from openwakeword.vad import VAD

        name = self.v.get("wake_word", "hey_jarvis")
        self.wake = Model(wakeword_models=[name], inference_framework="onnx")
        self.wake_key = next(iter(self.wake.models.keys()))
        self.vad = VAD()
        self.stt.warmup()

    def _print(self, who: str, text: str, style: str = ""):
        if self.console:
            self.console.print(f"[{style}]{who}[/] {text}" if style else f"{who} {text}")
        else:
            print(who, text)

    def _mic_cb(self, indata, frames, t, status):
        try:
            self._mic_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    # ------------------------------------------------------------------ run
    def run(self):
        import sounddevice as sd

        unsub = bus.subscribe(self._on_announce)
        if self.push_to_talk:
            threading.Thread(target=self._ptt_keys, daemon=True).start()
        hint = "Press Enter to talk." if self.push_to_talk else 'Say "Hey Jarvis".'
        self._print("●", f"Jarvis is listening. {hint} Ctrl+C to quit.", "bold green")
        self.speaker.say("Jarvis online.")
        try:
            with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=FRAME,
                                callback=self._mic_cb):
                self._loop()
        finally:
            unsub()

    def _ptt_keys(self):
        while True:
            try:
                input()
            except EOFError:
                return
            self._ptt.set()

    def _frames(self):
        while True:
            yield self._mic_q.get()

    def _loop(self):
        frames = self._frames()
        threshold = float(self.v.get("wake_threshold", 0.5))
        for frame in frames:
            if self.state in (State.IDLE, State.BUSY):
                score = self.wake.predict(frame)[self.wake_key]
                triggered = score >= threshold or self._ptt.is_set()
                if triggered:
                    self._ptt.clear()
                    self.wake.reset()
                    if self.state == State.BUSY:  # barge-in
                        self._turn_id += 1
                        self.speaker.stop()
                    beep("wake")
                    self._listen_and_handle(frames, timeout=5.0)
                    continue
                if self.state == State.BUSY and self._busy_done.is_set() and not self.speaker.speaking:
                    # reply finished → short follow-up window without the wake word
                    self.state = State.IDLE
                    follow = float(self.v.get("follow_up_s", 0))
                    if follow > 0:
                        time.sleep(0.25)
                        self._drain()
                        self._listen_and_handle(frames, timeout=follow, follow_up=True)

    def _drain(self):
        try:
            while True:
                self._mic_q.get_nowait()
        except queue.Empty:
            pass

    # --------------------------------------------------------------- listen
    def _record(self, frames, timeout: float) -> np.ndarray | None:
        """Record until end of speech. Returns None if nobody spoke within `timeout`."""
        self.state = State.LISTENING
        silence_needed = float(self.v.get("silence_ms", 800)) / 1000
        max_len = float(self.v.get("max_listen_s", 15))
        buf: list[np.ndarray] = []
        pre: list[np.ndarray] = []  # keep ~0.4 s before speech starts
        started = False
        silence = 0.0
        t0 = time.time()
        for frame in frames:
            speech = self.vad.predict(frame, frame_size=640) > 0.5
            if not started:
                pre = (pre + [frame])[-5:]
                if speech:
                    started = True
                    buf = list(pre)
                elif time.time() - t0 > timeout:
                    return None
                continue
            buf.append(frame)
            silence = 0.0 if speech else silence + FRAME / SR
            if silence >= silence_needed or len(buf) * FRAME / SR > max_len:
                break
        return np.concatenate(buf)

    def _listen_and_handle(self, frames, timeout: float, follow_up: bool = False):
        audio = self._record(frames, timeout)
        if audio is None:
            self.state = State.IDLE
            self.wake.reset()
            return
        beep("done")
        text = self.stt.transcribe(audio)
        if not text:
            self.state = State.IDLE
            if not follow_up:
                beep("error")
            return
        self._print("You:", text, "bold cyan")
        low = text.lower().strip(" .!?")
        if low in {"stop", "cancel", "never mind", "nevermind", "shut up", "quiet", "thanks", "thank you"}:
            self.speaker.stop()
            self.state = State.IDLE
            return
        if low in {"new conversation", "start over", "forget this conversation"}:
            self.brain.reset("voice")
            self.speaker.say("Fresh start.")
            self.state = State.IDLE
            return
        self._start_turn(text)

    # ---------------------------------------------------------------- think
    def _start_turn(self, text: str):
        self._turn_id += 1
        turn = self._turn_id
        self.state = State.BUSY
        self._busy_done.clear()
        self.wake.reset()

        def on_text(chunk: str):
            if turn == self._turn_id:  # ignore output from an interrupted turn
                self.speaker.feed(chunk)

        def work():
            try:
                reply = self.brain.ask(text, session="voice", channel="voice", on_text=on_text)
                if turn == self._turn_id:
                    self.speaker.flush()
                    self._print("Jarvis:", reply, "bold yellow")
            except Exception:
                log.exception("turn failed")
                if turn == self._turn_id:
                    self.speaker.say("Sorry, something went wrong.")
            finally:
                if turn == self._turn_id:
                    self._busy_done.set()

        threading.Thread(target=work, daemon=True, name="jarvis-turn").start()

    def _on_announce(self, text: str):
        self._print("Jarvis:", text, "bold magenta")
        beep("wake")
        self.speaker.say(text)
