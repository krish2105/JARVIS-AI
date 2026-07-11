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
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.brain.browser_tools import build_browser_tools
from src.brain.memory import memory_dispatch
from src.brain.tool_types import Tool
from src.brain.web_search import web_search
from src.system.config import Config

ConfirmFn = Callable[[str, str, dict], bool]  # (description, tool_name, input) -> confirmed?

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TOOL_CALLS_PATH = LOG_DIR / "jarvis_tool_calls.jsonl"
_logger = logging.getLogger("jarvis.tools")


def _ensure_logging() -> None:
    if _logger.handlers:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


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


def _run_shell(tool_input: dict) -> str:
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            tool_input["command"],
            shell=True,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds"
    output = (result.stdout + result.stderr).strip()
    return output[:4000] if output else "(no output)"


def _web_search(tool_input: dict) -> str:
    return web_search(tool_input["query"])


def _memory(tool_input: dict) -> str:
    return memory_dispatch(tool_input)


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
            description="Run a shell command in the project directory. 30s timeout.",
            parameters={"command": "string"},
            handler=_run_shell,
            confirm_key="run_shell_command",
        ),
        "web_search": Tool(
            name="web_search",
            description="Search the web and return the top results (title, url, snippet).",
            parameters={"query": "string"},
            handler=_web_search,
        ),
    }

    tools.update(build_browser_tools())
    return tools


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
            return f"run this shell command: {tool_input.get('command', '')}"
        return f"use {tool.name}"

    def log(self, tool_name: str, tool_input: dict, result: str) -> None:
        _logger.info("TOOL_CALL tool=%s input=%s result=%s", tool_name, tool_input, str(result)[:500])

        TOOL_CALLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "input": tool_input,
            "result": str(result)[:500],
        }
        with open(TOOL_CALLS_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
