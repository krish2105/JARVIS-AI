"""Minimal MCP (Model Context Protocol) client over the stdio transport.

We dropped the Claude Agent SDK, which had a full MCP client built in, so
this implements just enough of the MCP spec — the initialize handshake,
tools/list, and tools/call, over newline-delimited JSON-RPC 2.0 on a child
process's stdin/stdout — to drive @playwright/mcp for browser control.
It does not implement resources, prompts, or server-initiated notifications
beyond what's needed for that one use case.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import threading

MCP_PROTOCOL_VERSION = "2024-11-05"


class McpError(Exception):
    pass


class StdioMcpClient:
    def __init__(self, command: str, args: list[str], startup_timeout: float = 30.0):
        self.proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._id_counter = itertools.count(1)
        self._lock = threading.Lock()
        self._startup_timeout = startup_timeout
        self._initialize()

    def _send(self, method: str, params: dict | None = None, id_: int | None = None) -> None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if id_ is not None:
            message["id"] = id_
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def _read_response(self, expected_id: int) -> dict:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise McpError("MCP server closed stdout before responding")
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue  # server logging to stdout instead of stderr; ignore
            if data.get("id") == expected_id:
                return data

    def _call(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            request_id = next(self._id_counter)
            self._send(method, params, id_=request_id)
            response = self._read_response(request_id)
        if "error" in response:
            raise McpError(f"{method} failed: {response['error']}")
        return response.get("result", {})

    def _initialize(self) -> None:
        self._call(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "1.0.0"},
            },
        )
        self._send("notifications/initialized")

    def list_tools(self) -> list[dict]:
        result = self._call("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._call("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
