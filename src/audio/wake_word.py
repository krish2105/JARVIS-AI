"""On-device, always-listening wake word detection via Picovoice Porcupine.

Runs entirely locally — no audio ever leaves the machine at this stage.
Only the short utterance *after* wake-word detection gets recorded (see
recorder.py); wake_word.py itself only ever inspects small rolling frames
of mic audio and discards them immediately.
"""

from __future__ import annotations

from typing import Callable

import pvporcupine
import sounddevice as sd

from src.system.config import Config

# Stock Porcupine keywords that ship with the SDK and don't require training.
_STOCK_KEYWORDS = {
    "jarvis", "computer", "alexa", "americano", "blueberry", "bumblebee",
    "grapefruit", "grasshopper", "hey google", "hey siri", "ok google",
    "picovoice", "porcupine", "terminator",
}


def _keyword_args(cfg: Config) -> dict:
    if cfg.wake_word_model_path:
        return {"keyword_paths": [cfg.wake_word_model_path]}
    word = cfg.wake_word.lower()
    if word not in _STOCK_KEYWORDS:
        raise ValueError(
            f"'{word}' is not a stock Porcupine keyword and JARVIS_WAKE_WORD_MODEL_PATH "
            f"is not set. Either set JARVIS_WAKE_WORD to one of {sorted(_STOCK_KEYWORDS)}, "
            f"or train a custom 'Jarvis' model at https://console.picovoice.ai/ and point "
            f"JARVIS_WAKE_WORD_MODEL_PATH at the downloaded .ppn file."
        )
    return {"keywords": [word]}


class WakeWordListener:
    """Blocks on `listen_once()` until the configured wake word is heard."""

    def __init__(self, cfg: Config):
        if not cfg.picovoice_access_key:
            raise RuntimeError("PICOVOICE_ACCESS_KEY is not set in .env")

        self._porcupine = pvporcupine.create(
            access_key=cfg.picovoice_access_key,
            sensitivities=[cfg.audio.wake_word_sensitivity],
            **_keyword_args(cfg),
        )

    @property
    def sample_rate(self) -> int:
        return self._porcupine.sample_rate

    @property
    def frame_length(self) -> int:
        return self._porcupine.frame_length

    def listen_once(self, on_frame: Callable[[], None] | None = None) -> None:
        """Blocks until the wake word is detected once, then returns."""
        with sd.InputStream(
            samplerate=self._porcupine.sample_rate,
            blocksize=self._porcupine.frame_length,
            channels=1,
            dtype="int16",
        ) as stream:
            while True:
                pcm, _ = stream.read(self._porcupine.frame_length)
                pcm = pcm.reshape(-1)
                if self._porcupine.process(pcm) >= 0:
                    return
                if on_frame:
                    on_frame()

    def close(self) -> None:
        self._porcupine.delete()
