"""On-device text-to-speech via mlx-audio's Kokoro model.

Synthesizes sentence-by-sentence in a background thread while playback of
already-synthesized sentences proceeds on the main thread, so Jarvis starts
speaking before the whole reply has finished generating.
"""

from __future__ import annotations

import queue
import re
import threading

import numpy as np
import sounddevice as sd

MODEL_ID = "mlx-community/Kokoro-82M-bf16"
SAMPLE_RATE = 24000  # Kokoro's native output rate

_model = None
_model_lock = threading.Lock()

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from mlx_audio.tts.utils import load_model

            _model = load_model(MODEL_ID)
    return _model


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def speak(text: str, voice: str = "am_liam", speed: float = 1.0) -> None:
    """Synthesizes and plays `text` aloud, blocking until playback finishes."""
    sentences = _split_sentences(text)
    if not sentences:
        return

    model = _get_model()
    audio_queue: queue.Queue = queue.Queue(maxsize=4)

    def produce() -> None:
        try:
            for sentence in sentences:
                for chunk in model.generate(text=sentence, voice=voice, speed=speed, lang_code="a"):
                    audio_queue.put(np.array(chunk.audio, copy=False))
        finally:
            audio_queue.put(None)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()

    while True:
        item = audio_queue.get()
        if item is None:
            break
        sd.play(item, samplerate=SAMPLE_RATE)
        sd.wait()

    producer.join()
