"""macOS system control via osascript / open / pmset: volume, appearance,
launching apps, locking. Automation of System Events needs macOS Automation
permission; the first call prompts, and until granted these return a clear
message instead of failing silently.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("jarvis.system")

_NOT_AUTHORIZED = (
    "I need permission to control the system. Grant it in System Settings › "
    "Privacy & Security › Automation (allow Jarvis to control System Events)."
)


def _osa(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)


def _auth_error(stderr: str) -> bool:
    s = stderr.lower()
    return "not authorized" in s or "-1743" in s or "not allowed" in s


def get_volume() -> str:
    r = _osa("output volume of (get volume settings)")
    if r.returncode != 0:
        return "Couldn't read the volume."
    return f"Volume is at {r.stdout.strip()} percent."


def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    r = _osa(f"set volume output volume {level}")
    if r.returncode != 0:
        return "Couldn't set the volume."
    return f"Volume set to {level} percent."


def change_volume(delta: int) -> str:
    cur = _osa("output volume of (get volume settings)")
    if cur.returncode != 0:
        return "Couldn't read the volume."
    try:
        new = max(0, min(100, int(cur.stdout.strip()) + int(delta)))
    except ValueError:
        return "Couldn't read the volume."
    _osa(f"set volume output volume {new}")
    return f"Volume {'up' if delta >= 0 else 'down'} to {new} percent."


def set_mute(muted: bool) -> str:
    _osa(f"set volume output muted {'true' if muted else 'false'}")
    return "Muted." if muted else "Unmuted."


def toggle_dark_mode() -> str:
    r = _osa('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
    if r.returncode != 0:
        return _NOT_AUTHORIZED if _auth_error(r.stderr) else "Couldn't change the appearance."
    return "Toggled dark mode."


def set_dark_mode(on: bool) -> str:
    val = "true" if on else "false"
    r = _osa(f'tell application "System Events" to tell appearance preferences to set dark mode to {val}')
    if r.returncode != 0:
        return _NOT_AUTHORIZED if _auth_error(r.stderr) else "Couldn't change the appearance."
    return "Dark mode on." if on else "Light mode on."


def open_app(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Which app should I open?"
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return f"I couldn't find an app called {name}."
    return f"Opening {name}."


def lock_screen() -> str:
    # Puts the display to sleep; locks if "require password after sleep" is set.
    subprocess.run(["pmset", "displaysleepnow"], capture_output=True, text=True, timeout=10)
    return "Locking the screen."
