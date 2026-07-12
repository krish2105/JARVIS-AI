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
from datetime import datetime

logger = logging.getLogger("jarvis.apple")

# Calendar to create events in. The default personal calendar is named
# "Calendar" on a standard macOS setup.
_DEFAULT_CALENDAR = "Calendar"

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


# --- Calendar -------------------------------------------------------------

_LIST_EVENTS_SCRIPT = [
    "on run argv",
    "with timeout of 25 seconds",
    "set numDays to (item 1 of argv) as integer",
    'tell application "Calendar"',
    "set d1 to current date",
    "set hours of d1 to 0",
    "set minutes of d1 to 0",
    "set seconds of d1 to 0",
    "set d2 to d1 + numDays * days",
    'set out to ""',
    "repeat with c in calendars",
    "repeat with e in (every event of c whose start date ≥ d1 and start date < d2)",
    'set out to out & summary of e & " @ " & (start date of e as string) & linefeed',
    "end repeat",
    "end repeat",
    "end tell",
    "end timeout",
    "return out",
    "end run",
]


def list_events(days: int = 1) -> str:
    days = max(1, min(int(days), 14))
    try:
        r = _run_osascript(_LIST_EVENTS_SCRIPT, str(days))
    except subprocess.TimeoutExpired:
        return "Error: Calendar timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED.replace("Reminders", "Calendar")
        return f"Error reading calendar: {r.stderr.strip()[:200]}"
    lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return "No events on your calendar." if days == 1 else f"No events in the next {days} days."
    header = "Today:" if days == 1 else f"Next {days} days:"
    return header + " " + "; ".join(lines)


_CREATE_EVENT_SCRIPT = [
    "on run argv",
    "with timeout of 20 seconds",
    "set startDate to current date",
    "set year of startDate to (item 1 of argv) as integer",
    "set month of startDate to (item 2 of argv) as integer",
    "set day of startDate to (item 3 of argv) as integer",
    "set hours of startDate to (item 4 of argv) as integer",
    "set minutes of startDate to (item 5 of argv) as integer",
    "set seconds of startDate to 0",
    "set endDate to startDate + ((item 6 of argv) as integer) * minutes",
    'tell application "Calendar" to tell calendar (item 8 of argv)',
    "make new event with properties {summary:(item 7 of argv), start date:startDate, end date:endDate}",
    "end tell",
    "end timeout",
    "end run",
]


def create_event(title: str, start: str, duration_minutes: int = 60) -> str:
    title = (title or "").strip()
    if not title:
        return "Error: the event needs a title."
    try:
        dt = datetime.fromisoformat(start.replace("Z", "").strip())
    except (ValueError, AttributeError):
        return "Error: give the start time as 'YYYY-MM-DD HH:MM'."
    args = [
        str(dt.year), str(dt.month), str(dt.day), str(dt.hour), str(dt.minute),
        str(max(1, int(duration_minutes))), title, _DEFAULT_CALENDAR,
    ]
    try:
        r = _run_osascript(_CREATE_EVENT_SCRIPT, *args)
    except subprocess.TimeoutExpired:
        return "Error: Calendar timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED.replace("Reminders", "Calendar")
        return f"Error creating event: {r.stderr.strip()[:200]}"
    return f"Added “{title}” to your calendar on {dt.strftime('%b %d at %H:%M')}."
