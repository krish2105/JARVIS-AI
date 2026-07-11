"""Timers & alarms. Pure-stdlib, no permissions, no network.

The pipeline wires `notify` to speak aloud, so a timer announces itself when it
fires ("Your tea timer is done"). Kept process-local (one service instance
lives in src/brain/tools.py) so timers survive across turns within a run.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger("jarvis.timers")


class TimerService:
    def __init__(self, notify: Callable[[str], None] | None = None):
        self._timers: dict[int, dict] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self.notify = notify or (lambda msg: logger.info("timer fired: %s", msg))

    def add(self, seconds: float, label: str = "") -> int:
        if seconds <= 0:
            raise ValueError("duration must be positive")
        with self._lock:
            self._counter += 1
            tid = self._counter
            timer = threading.Timer(seconds, self._fire, args=(tid,))
            timer.daemon = True
            timer.start()
            self._timers[tid] = {"label": label, "timer": timer, "fire_at": time.time() + seconds}
        return tid

    def _fire(self, tid: int) -> None:
        with self._lock:
            info = self._timers.pop(tid, None)
        if info is None:
            return
        label = info["label"]
        msg = f"Your {label} timer is done." if label else "Your timer is done."
        try:
            self.notify(msg)
        except Exception:  # noqa: BLE001 - a notify failure must not crash the thread
            logger.exception("timer notify failed")

    def active(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self._lock:
            return [
                {"id": tid, "label": i["label"], "remaining": max(0, round(i["fire_at"] - now))}
                for tid, i in sorted(self._timers.items())
            ]

    def cancel(self, tid: int) -> bool:
        with self._lock:
            info = self._timers.pop(tid, None)
        if info is not None:
            info["timer"].cancel()
            return True
        return False
