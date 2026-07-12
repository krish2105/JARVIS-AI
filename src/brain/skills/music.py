"""Apple Music (Music.app) transport control via osascript. Needs macOS
Automation permission to control Music; the first call prompts.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("jarvis.music")

_NOT_AUTHORIZED = (
    "I need permission to control Music. Grant it in System Settings › "
    "Privacy & Security › Automation (allow Jarvis to control Music)."
)


def _music(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["osascript", "-e", f'tell application "Music" to {cmd}'],
        capture_output=True, text=True, timeout=10,
    )


def _auth(stderr: str) -> bool:
    s = stderr.lower()
    return "not authorized" in s or "-1743" in s or "not allowed" in s


def _do(cmd: str, ok: str) -> str:
    try:
        r = _music(cmd)
    except subprocess.TimeoutExpired:
        return "Music timed out."
    except FileNotFoundError:
        return "osascript not available."
    if r.returncode != 0:
        return _NOT_AUTHORIZED if _auth(r.stderr) else f"Couldn't control Music: {r.stderr.strip()[:120]}"
    return ok


def play() -> str:
    return _do("play", "Playing.")


def pause() -> str:
    return _do("pause", "Paused.")


def next_track() -> str:
    return _do("next track", "Skipped to the next track.")


def previous_track() -> str:
    return _do("previous track", "Back to the previous track.")


def current_track() -> str:
    try:
        r = _music('(get name of current track) & " — " & (get artist of current track)')
    except subprocess.TimeoutExpired:
        return "Music timed out."
    if r.returncode != 0:
        if _auth(r.stderr):
            return _NOT_AUTHORIZED
        return "Nothing is playing right now."
    return f"Now playing: {r.stdout.strip()}."


def play_playlist(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Which playlist?"
    r = subprocess.run(
        ["osascript", "-e", "on run argv", "-e", 'tell application "Music" to play playlist (item 1 of argv)',
         "-e", "end run", name],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        if _auth(r.stderr):
            return _NOT_AUTHORIZED
        return f"I couldn't find a playlist called {name}."
    return f"Playing the {name} playlist."
