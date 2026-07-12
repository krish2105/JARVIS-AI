"""Microphone coordination for spoken announcements (timers, routines).

Those fire while Jarvis is idle — when the wake-word listener has the mic open.
Playing TTS output while a mic input stream is open on the same device garbles
the audio (the same half-duplex constraint as barge-in). So an announcement
first asks the wake listener to release the mic, speaks, then lets it resume.
"""

from __future__ import annotations

import contextlib
import threading
import time


class MicCoordinator:
    def __init__(self) -> None:
        self._pause = threading.Event()      # set => the mic listener should release
        self._released = threading.Event()   # set => the listener has released

    # --- called by the wake listener --------------------------------------
    def should_pause(self) -> bool:
        return self._pause.is_set()

    def wait_while_paused(self) -> None:
        """Listener calls this once it has closed its stream: mark released and
        block until the announcement finishes."""
        self._released.set()
        while self._pause.is_set():
            time.sleep(0.05)

    # --- called by an announcement ----------------------------------------
    def request_pause(self, timeout: float = 2.0) -> None:
        self._released.clear()
        self._pause.set()
        self._released.wait(timeout)  # give the listener a moment to release the mic

    def resume(self) -> None:
        self._pause.clear()


mic_coordinator = MicCoordinator()


@contextlib.contextmanager
def mic_released():
    """Context manager: pause the wake-word mic for the duration, so an
    announcement can play cleanly, then resume it."""
    mic_coordinator.request_pause()
    try:
        yield
    finally:
        mic_coordinator.resume()
