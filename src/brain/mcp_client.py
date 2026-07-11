"""Minimal, supervised MCP (Model Context Protocol) client over stdio.

We dropped the Claude Agent SDK, which had a full MCP client built in, so this
implements just enough of the MCP spec — the initialize handshake, tools/list,
and tools/call, over newline-delimited JSON-RPC 2.0 on a child process's
stdin/stdout — to drive @playwright/mcp for browser control.

Hardened vs. the first version:
  • a background reader thread + queue so reads have a real timeout (a hung
    MCP server can't block a voice turn forever);
  • stderr is captured (bounded) instead of discarded, for diagnostics;
  • the child is supervised: `is_alive()` / `restart()`, and `call_tool`
    retries once across a dropped connection;
  • graceful shutdown.

MIGRATION NOTE: the official `mcp` Python SDK (PyPI `mcp`, requires Python
3.10+) is the intended long-term replacement for this module. It could not be
installed/verified in the audit environment (Python 3.9), so this hardened
hand-rolled client remains the verified path. See docs/IMPLEMENTATION_STATUS.md.
"""

from __future__ import annotations

import itertools
import json
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Callable

MCP_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0


class McpError(Exception):
    pass


def _default_spawn(command: str, args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [command, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


class StdioMcpClient:
    def __init__(
        self,
        command: str,
        args: list[str],
        read_timeout: float = _DEFAULT_TIMEOUT,
        spawn: Callable[[str, list[str]], subprocess.Popen] | None = None,
    ):
        self._command = command
        self._args = args
        self._read_timeout = read_timeout
        self._spawn = spawn or _default_spawn
        self._id_counter = itertools.count(1)
        self._call_lock = threading.Lock()
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._start()

    # --- lifecycle -------------------------------------------------------

    def _start(self) -> None:
        self.proc = self._spawn(self._command, self._args)
        self._incoming: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if self.proc.stderr is not None:
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._initialize()

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def restart(self) -> None:
        self.close()
        self._start()

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best-effort shutdown
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def stderr_tail(self) -> str:
        return "".join(self._stderr_tail)

    # --- transport -------------------------------------------------------

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        if stdout is None:
            return
        for line in iter(stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                self._incoming.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # server logging to stdout instead of stderr; ignore

    def _drain_stderr(self) -> None:
        stderr = self.proc.stderr
        if stderr is None:
            return
        for line in iter(stderr.readline, ""):
            self._stderr_tail.append(line)

    def _send(self, method: str, params: dict | None = None, id_: int | None = None) -> None:
        if self.proc.stdin is None:
            raise McpError("MCP server stdin is closed")
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if id_ is not None:
            message["id"] = id_
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError) as e:
            raise McpError(f"failed to write to MCP server: {e}") from e

    def _read_response(self, expected_id: int) -> dict:
        """Block until the response with `expected_id` arrives or the read
        timeout elapses (a hung server must not wedge the turn)."""
        while True:
            try:
                data = self._incoming.get(timeout=self._read_timeout)
            except queue.Empty as e:
                raise McpError(
                    f"MCP server timed out after {self._read_timeout}s"
                    + (f"; stderr: {self.stderr_tail()[-300:]}" if self._stderr_tail else "")
                ) from e
            if data.get("id") == expected_id:
                return data
            # A response for a different id (shouldn't happen under the lock) or
            # a server-initiated notification — ignore and keep waiting.

    def _call(self, method: str, params: dict | None = None) -> dict:
        with self._call_lock:
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

    # --- API -------------------------------------------------------------

    def list_tools(self) -> list[dict]:
        result = self._call("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            return self._call("tools/call", {"name": name, "arguments": arguments})
        except McpError:
            # One supervised retry across a dropped connection: restart the
            # child and try again before giving up.
            if self.is_alive():
                raise
            self.restart()
            return self._call("tools/call", {"name": name, "arguments": arguments})
