"""Scheduled routines: e.g. "every day at 8am, tell me the weather and my
calendar". A routine is a natural-language prompt run through Jarvis's brain at
a set time each day, spoken aloud and shown as a notification.

Persisted to ~/.jarvis/routines.json. A background thread checks the clock; the
pipeline supplies the trigger that actually runs + speaks the routine (with a
turn lock so it never runs the model concurrently with a live conversation).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("jarvis.routines")

DEFAULT_PATH = Path.home() / ".jarvis" / "routines.json"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class RoutineService:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._routines: list[dict] = self._load()
        self._trigger: Callable[[str], bool] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- persistence ------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            if self.path.exists():
                return json.loads(self.path.read_text())
        except Exception:  # noqa: BLE001
            logger.exception("could not read routines")
        return []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._routines, indent=2))
        except Exception:  # noqa: BLE001
            logger.exception("could not save routines")

    # --- CRUD -------------------------------------------------------------
    def add(self, time_str: str, prompt: str) -> str:
        time_str = (time_str or "").strip()
        prompt = (prompt or "").strip()
        if not _TIME_RE.match(time_str):
            return "Error: give the time as 24-hour HH:MM, e.g. 08:00."
        if not prompt:
            return "Error: what should the routine do?"
        # normalize to zero-padded HH:MM
        h, m = time_str.split(":")
        time_str = f"{int(h):02d}:{m}"
        with self._lock:
            rid = (max((r["id"] for r in self._routines), default=0)) + 1
            self._routines.append({"id": rid, "time": time_str, "prompt": prompt,
                                   "enabled": True, "last_fired": ""})
            self._save()
        return f"Routine #{rid} set for {time_str} daily: “{prompt}”."

    def remove(self, rid: int) -> str:
        with self._lock:
            before = len(self._routines)
            self._routines = [r for r in self._routines if r["id"] != rid]
            self._save()
        return f"Removed routine #{rid}." if len(self._routines) < before else f"No routine #{rid}."

    def listing(self) -> str:
        with self._lock:
            rs = list(self._routines)
        if not rs:
            return "You have no routines."
        return "; ".join(f"#{r['id']} at {r['time']}: {r['prompt']}" for r in rs)

    # --- scheduler --------------------------------------------------------
    def set_trigger(self, trigger: Callable[[str], bool]) -> None:
        self._trigger = trigger

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(20)

    def _tick(self) -> None:
        now = datetime.now()
        today, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        with self._lock:
            due = [r for r in self._routines
                   if r["enabled"] and r["time"] == hm and r.get("last_fired") != today]
        for r in due:
            fired = False
            if self._trigger is not None:
                try:
                    fired = bool(self._trigger(r["prompt"]))
                except Exception:  # noqa: BLE001
                    logger.exception("routine trigger failed")
            if fired:
                with self._lock:
                    r["last_fired"] = today
                    self._save()
