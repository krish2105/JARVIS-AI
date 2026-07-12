"""Apple Messages (iMessage/SMS) integration.

Reading history comes from the local Messages SQLite database
(~/Library/Messages/chat.db), opened READ-ONLY. That file is protected by
macOS's TCC: the process needs Full Disk Access. Without it, sqlite raises
"unable to open database file" — we catch that and return an actionable grant
message instead of a stack trace.

Sending goes through AppleScript (Automation permission), with the message
text passed via argv so it can't inject script. Sends are gated behind
confirmation in the tool layer (confirm_key="send_message").
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from pathlib import Path

logger = logging.getLogger("jarvis.messages")

_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

_FULL_DISK = (
    "I need Full Disk Access to read your messages. Grant it in System Settings › "
    "Privacy & Security › Full Disk Access (allow Jarvis), then try again."
)
_NOT_AUTHORIZED = (
    "I need permission to control Messages. Grant it in System Settings › Privacy & "
    "Security › Automation (allow Jarvis to control Messages), then try again."
)

# Apple stores message.date as nanoseconds since the 2001-01-01 epoch.
_APPLE_EPOCH_OFFSET = 978307200

_RECENT_SQL = """
SELECT
  datetime(message.date / 1000000000 + ?, 'unixepoch', 'localtime') AS ts,
  COALESCE(handle.id, 'unknown') AS who,
  message.is_from_me AS from_me,
  message.text AS body
FROM message
LEFT JOIN handle ON message.handle_id = handle.ROWID
WHERE message.text IS NOT NULL AND message.text != ''
ORDER BY message.date DESC
LIMIT ?;
"""


def recent_messages(count: int = 10) -> str:
    count = max(1, min(int(count), 25))
    if not _CHAT_DB.exists():
        return "No Messages history found on this Mac."
    try:
        # Read-only URI so we never lock or mutate the live database.
        conn = sqlite3.connect(f"file:{_CHAT_DB}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        return _FULL_DISK
    try:
        rows = conn.execute(_RECENT_SQL, (_APPLE_EPOCH_OFFSET, count)).fetchall()
    except sqlite3.OperationalError as e:
        if "unable to open" in str(e).lower() or "authorization" in str(e).lower():
            return _FULL_DISK
        return f"Error reading messages: {e}"
    finally:
        conn.close()
    if not rows:
        return "No recent text messages."
    lines = []
    for ts, who, from_me, body in reversed(rows):  # oldest→newest reads naturally
        speaker = "You" if from_me else (who or "Unknown")
        text = " ".join((body or "").split())  # collapse whitespace/newlines
        clock = ts[11:16] if ts and len(ts) >= 16 else ""
        lines.append(f"[{clock}] {speaker}: {text}")
    return "\n".join(lines)


_SEND_SCRIPT = [
    "on run argv",
    "with timeout of 20 seconds",
    'tell application "Messages"',
    "set svc to 1st account whose service type = iMessage",
    "set target to participant (item 1 of argv) of svc",
    "send (item 2 of argv) to target",
    "end tell",
    "end timeout",
    "end run",
]

# Fallback for setups where the account-based lookup fails: address the buddy
# directly on the first available service.
_SEND_FALLBACK = [
    "on run argv",
    'tell application "Messages" to send (item 2 of argv) to buddy (item 1 of argv) of (service 1)',
]


def _auth_error(stderr: str) -> bool:
    s = stderr.lower()
    return "not authorized" in s or "-1743" in s or "not allowed" in s


def send_message(to: str, text: str) -> str:
    to = (to or "").strip()
    text = text or ""
    if not to:
        return "Error: give a recipient (phone number, email, or contact handle)."
    if not text.strip():
        return "Error: there's no message to send."
    try:
        r = subprocess.run(
            ["osascript", *sum((["-e", ln] for ln in _SEND_SCRIPT), []), to, text],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode != 0 and not _auth_error(r.stderr):
            r = subprocess.run(
                ["osascript", *sum((["-e", ln] for ln in _SEND_FALLBACK), []), to, text],
                capture_output=True, text=True, timeout=25,
            )
    except subprocess.TimeoutExpired:
        return "Error: Messages timed out sending."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error sending message: {r.stderr.strip()[:200]}"
    return f"Message sent to {to}."
