"""On-device text-to-speech via mlx-audio's Kokoro model.

Audio is synthesized in a background thread and played through ONE continuous
output stream (not a fresh sd.play() per chunk), which is what keeps it smooth:
firing sd.play() repeatedly for each small chunk overlaps them and produces the
bursting/garbled noise. Writing sequential blocks to a single OutputStream has
no gaps and no overlap, and polling `should_stop` between blocks makes it
interruptible for barge-in.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger("jarvis.tts")

MODEL_ID = "mlx-community/Kokoro-82M-bf16"
SAMPLE_RATE = 24000  # Kokoro's native output rate
_BLOCK = 2400  # 0.1s of audio per write — barge-in stops within ~100 ms

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
    """Synthesize and play `text`, blocking until playback finishes. If
    `should_stop` fires, playback stops within ~100 ms (barge-in). Returns
    True if it was interrupted, False if it finished normally."""
    sentences = _split_sentences(text)
    if not sentences:
        return False

    model = _get_model()
    audio_queue: queue.Queue = queue.Queue(maxsize=16)

    def produce() -> None:
        try:
            for sentence in sentences:
                if should_stop is not None and should_stop():
                    break
                for chunk in model.generate(text=sentence, voice=voice, speed=speed, lang_code="a"):
                    audio_queue.put(np.asarray(chunk.audio, dtype=np.float32).reshape(-1))
        except Exception:  # noqa: BLE001 - a synth failure must not wedge playback
            logger.exception("TTS synthesis failed")
        finally:
            audio_queue.put(None)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()

    interrupted = False
    stopped = should_stop or (lambda: False)
    try:
        with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            while True:
                item = audio_queue.get()
                if item is None:
                    break
                for i in range(0, len(item), _BLOCK):
                    if stopped():
                        interrupted = True
                        break
                    stream.write(item[i : i + _BLOCK])
                if interrupted:
                    stream.abort()  # drop buffered audio immediately on barge-in
                    break
    except Exception:  # noqa: BLE001 - never let playback crash the turn
        logger.exception("TTS playback failed")
    finally:
        producer.join(timeout=1)
        # drain any leftover so a dead producer thread can exit
        try:
            while audio_queue.get_nowait() is not None:
                pass
        except queue.Empty:
            pass
    return interrupted
