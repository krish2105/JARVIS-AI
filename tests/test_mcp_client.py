"""Tests for the hardened stdio MCP client (src/brain/mcp_client.py) using an
injected fake child process — no real subprocess, no Node, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.mcp_client import McpError, StdioMcpClient  # noqa: E402


class _FakeStdin:
    def __init__(self):
        self.written: list[str] = []

    def write(self, s):
        self.written.append(s)

    def flush(self):
        pass


class _FakeStdout:
    """Serves pre-scripted response lines, then EOF ('')."""

    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _FakeProc:
    def __init__(self, lines):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = None
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def _line(id_, result):
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n"


def test_initialize_and_list_and_call():
    lines = [
        _line(1, {}),                                       # initialize
        _line(2, {"tools": [{"name": "browser_navigate"}]}),  # tools/list
        _line(3, {"content": [{"type": "text", "text": "ok"}]}),  # tools/call
    ]
    client = StdioMcpClient("fake", [], spawn=lambda c, a: _FakeProc(lines))
    tools = client.list_tools()
    assert tools == [{"name": "browser_navigate"}]
    result = client.call_tool("browser_navigate", {"url": "https://example.com"})
    assert result["content"][0]["text"] == "ok"
    # the initialize handshake + initialized notification were actually sent
    sent = "".join(client.proc.stdin.written)
    assert "initialize" in sent
    assert "notifications/initialized" in sent


def test_read_timeout_raises():
    # Only the initialize response is provided; a later call gets no reply.
    client = StdioMcpClient(
        "fake", [], read_timeout=0.2, spawn=lambda c, a: _FakeProc([_line(1, {})])
    )
    with pytest.raises(McpError):
        client.list_tools()


def test_error_response_raises():
    lines = [
        _line(1, {}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "boom"}}) + "\n",
    ]
    client = StdioMcpClient("fake", [], spawn=lambda c, a: _FakeProc(lines))
    with pytest.raises(McpError):
        client.list_tools()


def test_non_json_stdout_is_ignored():
    lines = [
        "starting up...\n",          # server logging to stdout, not JSON
        _line(1, {}),
        _line(2, {"tools": []}),
    ]
    client = StdioMcpClient("fake", [], spawn=lambda c, a: _FakeProc(lines))
    assert client.list_tools() == []
