"""On-device text-to-speech via mlx-audio's Kokoro model.

Synthesizes sentence-by-sentence in a background thread while playback of
already-synthesized sentences proceeds on the main thread, so Jarvis starts
speaking before the whole reply has finished generating.
"""

from __future__ import annotations

import queue
import re
import threading
from typing import Callable

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


def speak(
    text: str,
    voice: str = "am_liam",
    speed: float = 1.0,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Synthesizes and plays `text` aloud, blocking until playback finishes.

    If `should_stop` is supplied it is polled before each audio chunk (and
    the currently-playing chunk is checked while it plays); when it returns
    True, playback stops immediately — this is the TTS side of barge-in.
    Returns True if it was interrupted, False if it finished normally.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return False

    model = _get_model()
    audio_queue: queue.Queue = queue.Queue(maxsize=4)

    def produce() -> None:
        try:
            for sentence in sentences:
                if should_stop is not None and should_stop():
                    break
                for chunk in model.generate(text=sentence, voice=voice, speed=speed, lang_code="a"):
                    audio_queue.put(np.array(chunk.audio, copy=False))
        finally:
            audio_queue.put(None)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()

    interrupted = False
    while True:
        item = audio_queue.get()
        if item is None:
            break
        if should_stop is not None and should_stop():
            interrupted = True
            break
        sd.play(item, samplerate=SAMPLE_RATE)
        # Poll for a stop request while this chunk plays, instead of a blocking
        # sd.wait(), so barge-in stops audio within a poll interval.
        while sd.get_stream().active:
            if should_stop is not None and should_stop():
                sd.stop()
                interrupted = True
                break
            sd.sleep(50)  # ms
        if interrupted:
            break

    producer.join()
    return interrupted
