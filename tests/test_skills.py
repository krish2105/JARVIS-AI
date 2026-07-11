"""Tests for the timer and weather skills. Pure Python — the weather HTTP
getter is injected, so no network is used.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.skills.timers import TimerService  # noqa: E402
from src.brain.skills.weather import get_weather  # noqa: E402

# --- timers ---------------------------------------------------------------


def test_timer_fires_and_notifies():
    fired = []
    svc = TimerService(notify=fired.append)
    svc.add(0.15, label="tea")
    assert svc.active(), "timer should be active before firing"
    time.sleep(0.35)
    assert fired == ["Your tea timer is done."]
    assert svc.active() == []  # cleared after firing


def test_timer_active_reports_remaining():
    svc = TimerService(notify=lambda m: None)
    tid = svc.add(100, label="laundry")
    active = svc.active(now=svc._timers[tid]["fire_at"] - 42)
    assert active[0]["id"] == tid
    assert active[0]["label"] == "laundry"
    assert 41 <= active[0]["remaining"] <= 42


def test_timer_cancel():
    svc = TimerService(notify=lambda m: None)
    tid = svc.add(100)
    assert svc.cancel(tid) is True
    assert svc.active() == []
    assert svc.cancel(9999) is False


# --- weather --------------------------------------------------------------


def _fake_getter(geo_results, forecast):
    def get(url, params):
        if "geocoding" in url:
            return {"results": geo_results}
        return forecast
    return get


def test_weather_formats_current_and_highlow():
    get = _fake_getter(
        [{"name": "London", "country": "United Kingdom", "latitude": 51.5, "longitude": -0.1}],
        {"current": {"temperature_2m": 12.4, "weather_code": 3},
         "daily": {"temperature_2m_max": [15.1], "temperature_2m_min": [8.9]}},
    )
    out = get_weather("London", get=get)
    assert "London, United Kingdom" in out
    assert "12°C" in out
    assert "overcast" in out
    assert "High 15°, low 9°." in out


def test_weather_unknown_place():
    out = get_weather("Nowherexyz", get=_fake_getter([], {}))
    assert "couldn't find" in out.lower()


def test_weather_empty_location():
    assert get_weather("", get=_fake_getter([], {})).startswith("Error")
