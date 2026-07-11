"""Builds ClaudeAgentOptions: MCP server registration, tool allowlist, and
the confirmation + audit-log gate that guards every tool call.

Design note: `can_use_tool` only fires for tools that fall through to a
permission prompt — anything pre-approved via `allowed_tools` or a
permission_mode like `bypassPermissions` skips it entirely. `PreToolUse` /
`PostToolUse` hooks fire for *every* tool call regardless of approval
state, which is what Safety rule #3 ("log every tool call") and the
confirmation gate actually need. So we run with `permission_mode=
"bypassPermissions"` (no interactive prompt can block a hands-free voice
loop) and do all real enforcement — filesystem scoping, confirmation,
audit logging — in hooks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from src.brain.memory import memory_server
from src.system.config import Config

ConfirmFn = Callable[[str, str, dict], Awaitable[bool]]  # (description, tool_name, input) -> confirmed?

LOG_PATH = Path.home() / "Library" / "Logs" / "jarvis.log"
_logger = logging.getLogger("jarvis.tools")


def _ensure_logging() -> None:
    if _logger.handlers:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


# Maps an SDK tool_name to the human-readable action verb used in
# config.yaml's `require_confirmation_for`.
_CONFIRMATION_ALIASES: dict[str, str] = {
    "Bash": "run_shell_command",
}


def _tool_needs_confirmation(tool_name: str, tool_input: dict, require_confirmation_for: list[str]) -> tuple[bool, str]:
    """Returns (needs_confirmation, human_description)."""
    if tool_name in require_confirmation_for:
        if tool_name in ("Write", "Edit"):
            return True, f"write to {tool_input.get('file_path', 'a file')}"
        return True, f"use {tool_name}"

    alias = _CONFIRMATION_ALIASES.get(tool_name)
    if alias and alias in require_confirmation_for:
        return True, f"run this shell command: {tool_input.get('command', '')}"

    if tool_name.startswith("mcp__gmail__") and ("send" in tool_name or "draft" in tool_name):
        if "send_email" in require_confirmation_for:
            to = tool_input.get("to") or tool_input.get("recipient", "someone")
            return True, f"send/draft an email to {to}"

    if tool_name == "mcp__memory__memory" and tool_input.get("command") == "delete":
        if "delete_file" in require_confirmation_for:
            return True, f"delete memory file {tool_input.get('path')}"

    return False, ""


def _path_in_allowlist(path_str: str, allowlist: list[Path]) -> bool:
    try:
        resolved = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(resolved == root or root in resolved.parents for root in allowlist)


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_pre_tool_use_hook(cfg: Config, confirm_fn: ConfirmFn):
    allowlist = cfg.resolved_filesystem_allowlist()
    _ensure_logging()

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}

        # 1. Hard filesystem scoping — never negotiable, confirmation can't bypass it.
        if tool_name in ("Read", "Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            if file_path and not _path_in_allowlist(file_path, allowlist):
                reason = f"{file_path} is outside Jarvis's allowed directories {[str(p) for p in allowlist]}."
                _logger.info("DENY tool=%s input=%s reason=%s", tool_name, tool_input, reason)
                return _deny(reason)

        # 2. Confirmation gate for destructive / externally-visible actions.
        needs_confirmation, description = _tool_needs_confirmation(
            tool_name, tool_input, cfg.require_confirmation_for
        )
        if needs_confirmation:
            confirmed = await confirm_fn(description, tool_name, tool_input)
            if not confirmed:
                _logger.info("DENY tool=%s input=%s reason=not-confirmed", tool_name, tool_input)
                return _deny("User did not say 'confirm'.")
            _logger.info("CONFIRMED tool=%s input=%s", tool_name, tool_input)

        return {}

    return hook


async def audit_log_hook(input_data: dict, tool_use_id: str | None, context) -> dict:
    """PostToolUse hook: logs name, arguments, and result of every tool call
    that actually ran, per the safety rules (Safety rule #3)."""
    _ensure_logging()
    _logger.info("TOOL_CALL %s", {k: v for k, v in input_data.items() if k != "hook_event_name"})
    return {}


JARVIS_SYSTEM_PROMPT = """\
You are Jarvis, a capable, concise personal AI assistant running locally for your one user.
- Speak plainly and directly. Dry wit is welcome. Never use robotic filler like "As an AI...".
- Keep spoken replies short — you are being read aloud through text-to-speech, not displayed as \
a document. Prefer a couple of sentences over a bulleted essay unless the user asks for detail.
- Before any action that sends something externally (email, messages) or deletes/overwrites data, \
you must ask for confirmation and wait for the user to say "confirm" — this is enforced by the \
application layer, so always state exactly what you're about to do before attempting it.
- You have a persistent memory directory. Check it when it's relevant, and write down facts and \
preferences the user asks you to remember.
"""


def build_options(
    cfg: Config,
    confirm_fn: ConfirmFn,
    model: str | None = None,
    resume: str | None = None,
    extra_mcp_servers: dict | None = None,
) -> ClaudeAgentOptions:
    mcp_servers: dict = {"memory": memory_server}

    if cfg.gmail_mcp_url:
        mcp_servers["gmail"] = {
            "type": "http",
            "url": cfg.gmail_mcp_url,
            "headers": {"Authorization": f"Bearer {cfg.gmail_mcp_token}"} if cfg.gmail_mcp_token else {},
        }
    if cfg.gdrive_mcp_url:
        mcp_servers["gdrive"] = {
            "type": "http",
            "url": cfg.gdrive_mcp_url,
            "headers": {"Authorization": f"Bearer {cfg.gdrive_mcp_token}"} if cfg.gdrive_mcp_token else {},
        }
    mcp_servers["playwright"] = {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
    }

    if extra_mcp_servers:
        mcp_servers.update(extra_mcp_servers)

    return ClaudeAgentOptions(
        model=model or cfg.model.default,
        system_prompt=JARVIS_SYSTEM_PROMPT,
        # Nothing should block on an interactive prompt in a hands-free voice
        # loop — real enforcement happens in the PreToolUse hook below.
        permission_mode="bypassPermissions",
        hooks={
            "PreToolUse": [HookMatcher(hooks=[build_pre_tool_use_hook(cfg, confirm_fn)])],
            "PostToolUse": [HookMatcher(hooks=[audit_log_hook])],
        },
        mcp_servers=mcp_servers,
        cwd=str(Path(__file__).resolve().parents[2]),
        resume=resume,
    )
