"""Text-to-speech: Piper neural voices (default) with macOS `say` as fallback.

Speaker streams: feed it tokens as Claude generates them; every complete sentence is
synthesized and played while the next one is still being generated. stop() cuts
speech immediately (barge-in)."""
from __future__ import annotations

import io
import logging
import queue
import re
import subprocess
import threading
import wave

import numpy as np

from .. import config

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+|\n+")


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)      # markdown links -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = text.replace("°C", " degrees").replace("°F", " degrees Fahrenheit").replace("°", " degrees")
    text = text.replace("&", " and ").replace("%", " percent")
    return re.sub(r"\s+", " ", text).strip()


class Voice:
    """Synthesizes text to (int16 samples, sample_rate)."""

    def __init__(self):
        v = config.get()["voice"]
        self.engine = v.get("tts_engine", "piper")
        self.say_voice = v.get("say_voice", "Daniel")
        self.speed = float(v.get("speed", 1.0))
        self._piper = None
        self._syn_cfg = None
        if self.engine == "piper":
            try:
                self._load_piper(v["piper_voice"], v.get("piper_speaker"))
            except Exception as e:
                log.warning("Piper unavailable (%s); falling back to macOS say", e)
                self.engine = "say"

    def _load_piper(self, name: str, speaker):
        from piper import PiperVoice, SynthesisConfig

        path = config.MODELS_DIR / "voices" / f"{name}.onnx"
        if not path.exists():
            raise FileNotFoundError(f"{path} (run `jarvis setup`)")
        self._piper = PiperVoice.load(str(path))
        self._syn_cfg = SynthesisConfig(speaker_id=speaker, length_scale=self.speed)
        self.sample_rate = self._piper.config.sample_rate

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, np.int16), 22050
        if self.engine == "piper" and self._piper is not None:
            chunks = [c.audio_int16_array for c in self._piper.synthesize(text, syn_config=self._syn_cfg)]
            audio = np.concatenate(chunks) if chunks else np.zeros(0, np.int16)
            return audio, self.sample_rate
        return self._synth_say(text)

    def _synth_say(self, text: str) -> tuple[np.ndarray, int]:
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "say.wav")
            rate = int(185 / self.speed)
            subprocess.run(["say", "-v", self.say_voice, "-r", str(rate), "-o", out,
                            "--data-format=LEI16@22050", text], check=True)
            with wave.open(out) as w:
                return np.frombuffer(w.readframes(w.getnframes()), np.int16), w.getframerate()

    def wav_bytes(self, text: str) -> bytes:
        audio, sr = self.synth(text)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(audio.tobytes())
        return buf.getvalue()


class Speaker:
    """Plays speech on the Mac's speakers, sentence by sentence, interruptibly."""

    def __init__(self, voice: Voice | None = None):
        self.voice = voice or Voice()
        self._text_q: queue.Queue[str | None] = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=3)
        self._buf = ""
        self._stop = threading.Event()
        self._busy = 0
        self._busy_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        threading.Thread(target=self._synth_worker, daemon=True, name="tts-synth").start()
        threading.Thread(target=self._play_worker, daemon=True, name="tts-play").start()

    # -- public API ----------------------------------------------------------
    def feed(self, chunk: str) -> None:
        """Feed streamed text; complete sentences are queued for speech."""
        self._buf += chunk
        parts = _SENTENCE_END.split(self._buf)
        for p in parts[:-1]:
            self._enqueue(p)
        self._buf = parts[-1]

    def flush(self) -> None:
        if self._buf.strip():
            self._enqueue(self._buf)
        self._buf = ""

    def say(self, text: str) -> None:
        self.feed(text + "\n")
        self.flush()

    def stop(self) -> None:
        """Interrupt: drop everything queued and cut current playback."""
        self._stop.set()
        self._buf = ""
        for q in (self._text_q, self._audio_q):
            try:
                while True:
                    q.get_nowait()
                    self._done_one()
            except queue.Empty:
                pass

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    # -- internals -----------------------------------------------------------
    def _enqueue(self, text: str):
        if not clean_for_speech(text):
            return
        self._stop.clear()
        with self._busy_lock:
            self._busy += 1
            self._idle.clear()
        self._text_q.put(text)

    def _done_one(self):
        with self._busy_lock:
            self._busy = max(0, self._busy - 1)
            if self._busy == 0:
                self._idle.set()

    def _synth_worker(self):
        while True:
            text = self._text_q.get()
            if self._stop.is_set():
                self._done_one()
                continue
            try:
                audio, sr = self.voice.synth(text)
            except Exception:
                log.exception("TTS synth failed")
                self._done_one()
                continue
            self._audio_q.put((audio, sr))

    def _play_worker(self):
        import sounddevice as sd

        while True:
            audio, sr = self._audio_q.get()
            try:
                if not self._stop.is_set() and audio.size:
                    block = sr // 20  # 50 ms blocks -> quick to interrupt
                    with sd.OutputStream(samplerate=sr, channels=1, dtype="int16") as out:
                        for i in range(0, len(audio), block):
                            if self._stop.is_set():
                                break
                            out.write(audio[i:i + block])
            except Exception:
                log.exception("audio playback failed")
            finally:
                self._done_one()


def beep(kind: str = "wake") -> None:
    """Short UI sounds (non-blocking)."""
    import sounddevice as sd

    sr = 22050

    def tone(f, ms, vol=0.25):
        t = np.arange(int(sr * ms / 1000)) / sr
        env = np.minimum(1, np.minimum(t, t[::-1]) * 80)
        return (np.sin(2 * np.pi * f * t) * env * vol)

    if kind == "wake":
        sig = np.concatenate([tone(660, 70), tone(990, 90)])
    elif kind == "done":
        sig = tone(520, 60, 0.15)
    else:  # error
        sig = np.concatenate([tone(400, 90), tone(300, 120)])
    try:
        sd.play((sig * 32767).astype(np.int16), sr)
    except Exception:
        log.debug("beep failed", exc_info=True)
