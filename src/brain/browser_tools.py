"""Wraps @playwright/mcp's tools (browser_navigate, browser_click, etc.) as
local Tool entries, discovered dynamically over the minimal MCP stdio client
in mcp_client.py. Degrades silently (returns no tools) if Node/npx isn't
installed or the MCP server fails to start, so browser control is simply
unavailable rather than crashing the whole assistant.
"""

from __future__ import annotations

import logging
import shutil

from src.brain.mcp_client import StdioMcpClient
from src.brain.tool_types import Tool

logger = logging.getLogger("jarvis.browser_tools")

# Browser MCP tools whose names contain one of these substrings are treated
# as READ_ONLY — they observe the page without acting on the world, so they
# run without confirmation. Everything else (navigate, click, type, submit,
# upload, select, drag, dialog handling, tab manipulation) can cause an
# external side effect on a page the model does not fully control, and is
# gated behind the "browser_action" confirmation key. Browser page content
# is untrusted input; a mutating action driven by injected page text must
# not fire without the user approving it.
_BROWSER_READONLY_MARKERS = (
    "snapshot", "screenshot", "console", "network", "wait", "tab_list",
    "list_tabs", "pdf_save",
)


def _is_readonly(remote_name: str) -> bool:
    name = remote_name.lower()
    return any(marker in name for marker in _BROWSER_READONLY_MARKERS)


def _stringify_result(result: dict) -> str:
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    return str(result)


def build_browser_tools() -> dict[str, Tool]:
    if not shutil.which("npx"):
        logger.info("npx not found; browser-control tools disabled.")
        return {}

    try:
        client = StdioMcpClient("npx", ["-y", "@playwright/mcp@latest"])
        remote_tools = client.list_tools()
    except Exception:
        logger.exception("Failed to start @playwright/mcp; browser-control tools disabled.")
        return {}

    tools: dict[str, Tool] = {}
    for spec in remote_tools:
        remote_name = spec["name"]
        local_name = f"browser_{remote_name}"

        def handler(tool_input: dict, _client=client, _remote_name=remote_name) -> str:
            return _stringify_result(_client.call_tool(_remote_name, tool_input))

        tools[local_name] = Tool(
            name=local_name,
            description=spec.get("description", ""),
            parameters=spec.get("inputSchema", {}),
            handler=handler,
            confirm_key=None if _is_readonly(remote_name) else "browser_action",
        )
    return tools
