"""Tests for the routine service (src/brain/skills/routines.py) and the mic
coordinator (src/audio/coordinator.py). Pure Python.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio.coordinator import MicCoordinator  # noqa: E402
from src.brain.skills.routines import RoutineService  # noqa: E402


def test_add_normalizes_time_and_lists(tmp_path):
    svc = RoutineService(path=tmp_path / "r.json")
    out = svc.add("8:00", "tell me the weather")
    assert "08:00" in out
    assert "tell me the weather" in svc.listing()


def test_add_rejects_bad_time_and_empty_prompt(tmp_path):
    svc = RoutineService(path=tmp_path / "r.json")
    assert svc.add("25:00", "x").startswith("Error")
    assert svc.add("8:99", "x").startswith("Error")
    assert svc.add("08:00", "").startswith("Error")


def test_remove(tmp_path):
    svc = RoutineService(path=tmp_path / "r.json")
    svc.add("07:30", "brief")
    rid = svc._routines[0]["id"]
    assert "Removed" in svc.remove(rid)
    assert svc.listing() == "You have no routines."
    assert "No routine" in svc.remove(999)


def test_persists_across_instances(tmp_path):
    p = tmp_path / "r.json"
    RoutineService(path=p).add("09:30", "morning brief")
    assert "morning brief" in RoutineService(path=p).listing()


def test_trigger_fires_at_matching_time(tmp_path, monkeypatch):
    import src.brain.skills.routines as routines_mod

    svc = RoutineService(path=tmp_path / "r.json")
    svc.add("08:00", "brief")

    # Freeze "now" to the routine's minute.
    class _FakeNow:
        def strftime(self, fmt):
            return "2026-07-12" if fmt == "%Y-%m-%d" else "08:00"

    monkeypatch.setattr(routines_mod, "datetime", type("D", (), {"now": staticmethod(lambda: _FakeNow())}))

    fired = []
    svc.set_trigger(lambda prompt: (fired.append(prompt), True)[1])
    svc._tick()  # one scheduler check
    assert fired == ["brief"]
    # last_fired recorded so it won't double-fire
    assert svc._routines[0]["last_fired"] == "2026-07-12"
    svc._tick()  # second tick same minute -> no re-fire
    assert fired == ["brief"]


# --- mic coordinator ------------------------------------------------------


def test_mic_coordinator_pause_resume():
    c = MicCoordinator()
    assert c.should_pause() is False

    released = []

    def listener():
        # simulate the wake listener noticing the pause and releasing
        while not c.should_pause():
            time.sleep(0.01)
        c.wait_while_paused()
        released.append("resumed")

    t = threading.Thread(target=listener, daemon=True)
    t.start()
    c.request_pause(timeout=1)
    assert c.should_pause() is True
    c.resume()
    t.join(timeout=1)
    assert released == ["resumed"]
