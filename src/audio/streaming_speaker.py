"""Incremental text-to-speech.

Instead of waiting for the whole reply and then speaking it, this speaks each
sentence AS the model streams it: the first sentence starts playing while the
model is still generating the rest, so Jarvis begins talking seconds sooner.

It plugs into the token stream from src/brain/agent.run_turn(on_token=...):
each incoming chunk is buffered, complete sentences are peeled off and queued,
and a worker thread speaks them in order. A CancellationToken (barge-in) stops
playback immediately and drains the queue without speaking the rest.

Pure-logic parts (sentence segmentation, queueing) are unit-testable by
injecting a fake `speak_fn`; the default calls the real Kokoro TTS.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable

from src.core.cancellation import CancellationToken

logger = logging.getLogger("jarvis.tts.stream")

# Split on sentence-ending punctuation followed by whitespace. A decimal like
# "3.5" has no trailing space so it is not split; "Dr. Smith" is an accepted
# minor over-split.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_complete(buffer: str) -> tuple[list[str], str]:
    """Return (complete_sentences, remainder). The remainder is the trailing
    partial sentence still being generated."""
    parts = _SENTENCE_BOUNDARY.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    complete = [p for p in parts[:-1] if p.strip()]
    return complete, parts[-1]


def _default_speak(text: str, voice: str, should_stop: Callable[[], bool]) -> None:
    from src.audio.tts import speak

    speak(text, voice=voice, should_stop=should_stop)


class StreamingSpeaker:
    def __init__(
        self,
        voice: str,
        cancel: CancellationToken | None = None,
        speak_fn: Callable[[str, str, Callable[[], bool]], None] | None = None,
    ):
        self._voice = voice
        self._cancel = cancel
        self._speak_fn = speak_fn or _default_speak
        self._buffer = ""
        self._queue: queue.Queue = queue.Queue()
        self.spoken: list[str] = []  # sentences actually handed to TTS (for tests/inspection)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.cancelled

    def feed(self, chunk: str) -> None:
        """Feed a streamed text chunk (a TokenSink for run_turn)."""
        self._buffer += chunk
        complete, self._buffer = split_complete(self._buffer)
        for sentence in complete:
            self._queue.put(sentence)

    def finish(self, timeout: float | None = None) -> None:
        """Flush the trailing partial sentence and wait for playback to drain."""
        tail = self._buffer.strip()
        self._buffer = ""
        if tail and not self._cancelled():
            self._queue.put(tail)
        self._queue.put(None)  # sentinel
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            sentence = self._queue.get()
            if sentence is None:
                return
            if self._cancelled():
                continue  # barge-in: drain remaining sentences without speaking
            self.spoken.append(sentence)
            try:
                self._speak_fn(sentence, self._voice, self._cancelled)
            except Exception:  # noqa: BLE001 - a TTS failure must not kill the turn
                logger.exception("streaming TTS failed on sentence")
