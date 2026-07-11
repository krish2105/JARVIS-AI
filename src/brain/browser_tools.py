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
        )
    return tools
