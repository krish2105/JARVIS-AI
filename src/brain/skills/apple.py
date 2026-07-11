"""Apple Reminders integration via AppleScript (osascript).

Text is passed through osascript's `argv`, never interpolated into the script
source, so a reminder title can't inject AppleScript. Controlling Reminders
needs macOS Automation permission — the first call triggers the system prompt;
until it's granted these return a clear, actionable message instead of failing
opaquely.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("jarvis.apple")

_NOT_AUTHORIZED = (
    "I need permission to control Reminders. Grant it in System Settings › "
    "Privacy & Security › Automation (allow Jarvis to control Reminders), then try again."
)


def _run_osascript(lines: list[str], *args: str) -> subprocess.CompletedProcess:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def _auth_error(stderr: str) -> bool:
    s = stderr.lower()
    return "not authorized" in s or "-1743" in s or "not allowed" in s


def create_reminder(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "Error: nothing to remind you about."
    try:
        r = _run_osascript(
            [
                "on run argv",
                'tell application "Reminders" to make new reminder with properties {name: item 1 of argv}',
                "end run",
            ],
            text,
        )
    except subprocess.TimeoutExpired:
        return "Error: Reminders timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error creating reminder: {r.stderr.strip()[:200]}"
    return f"Reminder added: “{text}”."


def list_reminders() -> str:
    try:
        r = _run_osascript(
            ['tell application "Reminders" to get name of reminders whose completed is false'],
        )
    except subprocess.TimeoutExpired:
        return "Error: Reminders timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error reading reminders: {r.stderr.strip()[:200]}"
    names = [n.strip() for n in r.stdout.strip().split(",") if n.strip()]
    if not names:
        return "You have no open reminders."
    return "Open reminders: " + "; ".join(names)
