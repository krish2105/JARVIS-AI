"""Weather via Open-Meteo — free, no API key, no signup.

Geocodes the place name, then fetches current conditions + today's high/low.
The HTTP getter is injectable so the formatting logic is unit-testable without
network access.
"""

from __future__ import annotations

from collections.abc import Callable

import requests

# WMO weather-interpretation codes -> short descriptions.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}

Getter = Callable[[str, dict], dict]


def _http_get(url: str, params: dict) -> dict:
    return requests.get(url, params=params, timeout=10).json()


def get_weather(location: str, get: Getter = _http_get) -> str:
    location = (location or "").strip()
    if not location:
        return "Error: no location given."
    try:
        geo = get("https://geocoding-api.open-meteo.com/v1/search", {"name": location, "count": 1})
    except Exception as e:  # noqa: BLE001
        return f"Error: weather lookup failed ({e})."
    results = geo.get("results") or []
    if not results:
        return f"I couldn't find a place called {location}."

    place = results[0]
    name = place.get("name", location)
    country = place.get("country", "")
    try:
        w = get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": place["latitude"], "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
            },
        )
    except Exception as e:  # noqa: BLE001
        return f"Error: weather lookup failed ({e})."

    cur = w.get("current", {})
    daily = w.get("daily", {})
    temp = cur.get("temperature_2m")
    desc = _WMO.get(cur.get("weather_code"), "")
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []

    where = f"{name}, {country}".rstrip(", ")
    head = f"{where}: {round(temp)}°C" if temp is not None else where
    if desc:
        head += f", {desc}"
    if highs and lows and highs[0] is not None and lows[0] is not None:
        head += f". High {round(highs[0])}°, low {round(lows[0])}°."
    return head
