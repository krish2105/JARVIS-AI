"""Barge-in: interrupt Jarvis mid-turn by saying the wake word again.

While Jarvis is thinking or speaking, this runs the wake-word detector on a
background thread. If it hears "Hey Jarvis" again, it fires the turn's
CancellationToken — which stops generation, stops any pending tool from
running, and stops TTS playback (see StreamingSpeaker / run_turn) — and marks
`triggered`, so the pipeline knows to capture a fresh command instead of
finishing the interrupted one.

Wake-word barge-in (rather than any-speech VAD) is deliberate: there is no
acoustic echo cancellation here, so the mic hears Jarvis's own TTS. Requiring
the full "Hey Jarvis" phrase makes a self-interrupt from Jarvis's own voice
very unlikely, whereas plain voice-activity detection would trip on it.

The wake object is injected, so the trigger→cancel logic is unit-testable with
a fake detector.
"""

from __future__ import annotations

import logging
import threading

from src.core.cancellation import CancellationToken

logger = logging.getLogger("jarvis.bargein")


class BargeInWatcher:
    def __init__(self, wake, cancel: CancellationToken):
        """`wake` needs a `detect_until(stop_event) -> bool` method
        (src/audio/wake_word.WakeWordListener)."""
        self._wake = wake
        self._cancel = cancel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.triggered = False

    def start(self) -> None:
        self._stop.clear()
        self.triggered = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            if self._wake.detect_until(self._stop):
                self.triggered = True
                self._cancel.cancel()
                logger.info("barge-in: wake word heard mid-turn, cancelling")
        except Exception:  # noqa: BLE001 - a watcher failure must not crash the turn
            logger.exception("barge-in watcher failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
