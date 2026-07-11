"""On-device, always-listening wake word detection via openWakeWord.

No account, no API key, nothing to sign up for — openWakeWord is fully
open-source and ships a pretrained "hey jarvis" model out of the box,
downloaded once from its own model repo (free, no key) and cached locally.

Runs entirely locally — no audio ever leaves the machine at this stage.
Only the short utterance *after* wake-word detection gets recorded (see
recorder.py); wake_word.py itself only ever inspects small rolling frames
of mic audio and discards them immediately.
"""

from __future__ import annotations

from collections.abc import Callable

import sounddevice as sd

from src.system.config import Config

SAMPLE_RATE = 16000
FRAME_LENGTH = 1280  # 80ms at 16kHz, openWakeWord's recommended chunk size


class WakeWordListener:
    """Blocks on `listen_once()` until the configured wake word is heard."""

    def __init__(self, cfg: Config):
        import openwakeword
        from openwakeword.model import Model

        # Idempotent: no-ops if models are already downloaded/cached.
        openwakeword.utils.download_models()

        model_kwargs = {}
        if cfg.wake_word_model_path:
            model_kwargs["wakeword_models"] = [cfg.wake_word_model_path]
        self._model = Model(**model_kwargs)  # no filter -> loads all bundled models

        self.wake_word = cfg.wake_word.lower()
        self.threshold = cfg.audio.wake_word_sensitivity
        self.sample_rate = SAMPLE_RATE
        self.frame_length = FRAME_LENGTH

    def _matched_score(self, scores: dict[str, float]) -> float | None:
        """openWakeWord's model names look like 'hey_jarvis_v0.1'; match
        loosely on the configured wake word rather than an exact key so we
        don't hardcode a model filename that might change across releases."""
        for name, score in scores.items():
            if self.wake_word in name.lower() and score >= self.threshold:
                return score
        return None

    def listen_once(self, on_frame: Callable[[], None] | None = None) -> None:
        """Blocks until the wake word is detected once, then returns."""
        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.frame_length,
            channels=1,
            dtype="int16",
        ) as stream:
            while True:
                pcm, _ = stream.read(self.frame_length)
                pcm = pcm.reshape(-1)
                scores = self._model.predict(pcm)
                if self._matched_score(scores) is not None:
                    self._model.reset()
                    return
                if on_frame:
                    on_frame()

    def close(self) -> None:
        pass  # no persistent OS resource beyond the model object itself
