"""Apple Mail integration via AppleScript (osascript).

Reads unread mail and searches the inbox (read-only, safe), and composes +
sends a message (gated behind confirmation in the tool layer). All user text is
passed through osascript's `argv`, never interpolated into the script source,
so a subject or address can't inject AppleScript. Controlling Mail needs macOS
Automation permission — the first call triggers the system prompt; until it's
granted these return a clear, actionable message instead of failing opaquely.

Mail.app must have the account configured (the user's Gmail, etc.). If it
isn't, the read calls simply return "no unread mail".
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("jarvis.mail")

_NOT_AUTHORIZED = (
    "I need permission to control Mail. Grant it in System Settings › Privacy & "
    "Security › Automation (allow Jarvis to control Mail), then try again."
)

# US-ASCII record separators used to split AppleScript output unambiguously —
# safer than commas/newlines that can appear inside a subject or sender.
_FS = ""  # field separator
_RS = ""  # record separator


def _run_osascript(lines: list[str], *args: str, timeout: int = 25) -> subprocess.CompletedProcess:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _auth_error(stderr: str) -> bool:
    s = stderr.lower()
    return "not authorized" in s or "-1743" in s or "not allowed" in s


def _messages_script(where: str) -> list[str]:
    """Build a script that lists up to N inbox messages matching `where`,
    each as 'sender <FS> subject', records separated by <RS>."""
    return [
        "on run argv",
        "with timeout of 20 seconds",
        "set maxN to (item 1 of argv) as integer",
        'tell application "Mail"',
        f"set msgs to (messages of inbox whose {where})",
        'set out to ""',
        "set c to 0",
        "repeat with m in msgs",
        "if c ≥ maxN then exit repeat",
        f'set out to out & (sender of m) & "{_FS}" & (subject of m) & "{_RS}"',
        "set c to c + 1",
        "end repeat",
        "end tell",
        "end timeout",
        "return out",
        "end run",
    ]


def _parse_messages(raw: str) -> list[dict]:
    out = []
    for rec in raw.split(_RS):
        rec = rec.strip()
        if not rec:
            continue
        sender, _, subject = rec.partition(_FS)
        out.append({"sender": sender.strip(), "subject": subject.strip()})
    return out


def _format(items: list[dict], empty: str) -> str:
    if not items:
        return empty
    lines = [f"{i + 1}. {m['subject'] or '(no subject)'} — {m['sender']}" for i, m in enumerate(items)]
    return "\n".join(lines)


def list_unread(count: int = 5) -> str:
    count = max(1, min(int(count), 15))
    try:
        r = _run_osascript(_messages_script("read status is false"), str(count))
    except subprocess.TimeoutExpired:
        return "Error: Mail timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error reading mail: {r.stderr.strip()[:200]}"
    items = _parse_messages(r.stdout)
    return _format(items, "No unread mail. Inbox zero.")


def search_mail(query: str, count: int = 5) -> str:
    query = (query or "").strip()
    if not query:
        return "Error: what should I search your mail for?"
    count = max(1, min(int(count), 15))
    # `query` is passed as argv item 2 and referenced inside the whose-clause.
    script = [
        "on run argv",
        "with timeout of 20 seconds",
        "set maxN to (item 1 of argv) as integer",
        "set q to item 2 of argv",
        'tell application "Mail"',
        "set msgs to (messages of inbox whose subject contains q)",
        'set out to ""',
        "set c to 0",
        "repeat with m in msgs",
        "if c ≥ maxN then exit repeat",
        f'set out to out & (sender of m) & "{_FS}" & (subject of m) & "{_RS}"',
        "set c to c + 1",
        "end repeat",
        "end tell",
        "end timeout",
        "return out",
        "end run",
    ]
    try:
        r = _run_osascript(script, str(count), query)
    except subprocess.TimeoutExpired:
        return "Error: Mail timed out."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error searching mail: {r.stderr.strip()[:200]}"
    items = _parse_messages(r.stdout)
    return _format(items, f"No inbox messages matching “{query}”.")


_SEND_SCRIPT = [
    "on run argv",
    "with timeout of 20 seconds",
    'tell application "Mail"',
    "set newMsg to make new outgoing message with properties "
    "{subject:(item 2 of argv), content:(item 3 of argv), visible:false}",
    "tell newMsg",
    "make new to recipient at end of to recipients with properties {address:(item 1 of argv)}",
    "send",
    "end tell",
    "end tell",
    "end timeout",
    "end run",
]


def send_email(to: str, subject: str, body: str) -> str:
    to = (to or "").strip()
    if not to or "@" not in to:
        return "Error: give a valid recipient email address."
    subject = (subject or "").strip()
    body = body or ""
    try:
        r = _run_osascript(_SEND_SCRIPT, to, subject, body)
    except subprocess.TimeoutExpired:
        return "Error: Mail timed out sending."
    except FileNotFoundError:
        return "Error: osascript not available."
    if r.returncode != 0:
        if _auth_error(r.stderr):
            return _NOT_AUTHORIZED
        return f"Error sending mail: {r.stderr.strip()[:200]}"
    return f"Email sent to {to}: “{subject or '(no subject)'}”."
