"""Cooperative cancellation for a voice turn.

When the user barges in (starts speaking while Jarvis is thinking or talking),
the in-flight turn must stop: generation should stop producing tokens, the tool
loop should stop before the next tool, TTS should stop queuing audio, and any
approval that was pending must be invalidated so a late "confirm" can't fire it.

Python has no safe way to kill a running thread, so cancellation is
*cooperative*: long-running work checks `token.cancelled` at safe points and
bails out. This module is pure and thread-safe, and is unit-testable without
any audio or model dependency.
"""

from __future__ import annotations

import threading


class CancellationToken:
    """A one-way flag. `cancel()` sets it; workers poll `cancelled` or call
    `raise_if_cancelled()` at safe checkpoints."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledTurn()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or timeout; returns True if cancelled."""
        return self._event.wait(timeout)


class CancelledTurn(Exception):
    """Raised by `raise_if_cancelled()` to unwind a cancelled turn."""
