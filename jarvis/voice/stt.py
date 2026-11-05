"""Speech-to-text with faster-whisper (local, CPU, int8)."""
from __future__ import annotations

import io
import logging
import threading

import numpy as np

from .. import config

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_size: str | None = None):
        self.model_size = model_size or config.get()["voice"]["whisper_model"]
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("loading whisper %s", self.model_size)
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8",
                download_root=str(config.MODELS_DIR / "whisper"),
            )
        return self._model

    def warmup(self):
        self.transcribe(np.zeros(16000, np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: 16 kHz mono, int16 or float32."""
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        if audio.size < 16000 * 0.3:
            return ""
        with self._lock:
            segs, _ = self._get().transcribe(
                audio, language="en", beam_size=1, vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt="Jarvis, set a timer, remind me, what's the weather.",
            )
            text = " ".join(s.text.strip() for s in segs).strip()
        # Whisper hallucinates these on silence/noise
        if text.lower().strip(" .!") in {"", "you", "thank you", "thanks for watching", "bye"}:
            return ""
        return text

    def transcribe_bytes(self, data: bytes) -> str:
        """Decode any audio container (webm/ogg/wav/m4a) and transcribe."""
        return self.transcribe(decode_audio(data))


def decode_audio(data: bytes, sr: int = 16000) -> np.ndarray:
    import av

    out = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        for frame in container.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0


_shared: Transcriber | None = None
_shared_lock = threading.Lock()


def shared() -> Transcriber:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Transcriber()
        return _shared
