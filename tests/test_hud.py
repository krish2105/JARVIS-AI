"""Tests for the HUD WebSocket relay (src/hud/server.py) and the pipeline's
state reporter client (src/hud/client.py) — the mechanism that replaced
direct in-process calls once the voice pipeline and the HUD window were
split into separate processes (see src/main.py's docstring for why).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

from src.hud.client import HudStateReporter  # noqa: E402
from src.hud.server import (  # noqa: E402
    HudServer,  # noqa: E402
    _DropHandshakeRejections,
    _quiet_handshake_rejections,
)
from src.system.config import Config  # noqa: E402


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("websockets.server", logging.ERROR, __file__, 0, msg, None, None)


def test_handshake_rejection_records_are_dropped():
    filt = _DropHandshakeRejections()
    # The benign Origin-rejection log is dropped...
    assert filt.filter(_record("opening handshake failed")) is False
    # ...but a genuine server error still gets through.
    assert filt.filter(_record("connection handler failed: boom")) is True


def test_quiet_filter_is_installed_once():
    ws_logger = logging.getLogger("websockets.server")
    ws_logger.filters = [f for f in ws_logger.filters if not isinstance(f, _DropHandshakeRejections)]
    _quiet_handshake_rejections()
    _quiet_handshake_rejections()  # idempotent
    installed = [f for f in ws_logger.filters if isinstance(f, _DropHandshakeRejections)]
    assert len(installed) == 1


class FakeWebSocket:
    """Minimal stand-in for a websockets connection: records what's sent
    to it and replays a fixed list of incoming messages on iteration."""

    def __init__(self, incoming_messages: list[str] | None = None):
        self._incoming = list(incoming_messages or [])
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


def test_broadcast_updates_latest_and_fires_on_state():
    received = []
    server = HudServer(Config(), on_state=received.append)

    asyncio.run(server._broadcast({"state": "listening"}))

    assert server._latest == {"state": "listening"}
    assert received == [{"state": "listening"}]


def test_broadcast_with_no_clients_does_not_error():
    server = HudServer(Config())
    asyncio.run(server._broadcast({"state": "idle"}))
    assert server._latest == {"state": "idle"}


def test_handler_relays_incoming_client_message():
    server = HudServer(Config())
    ws = FakeWebSocket(['{"state": "speaking", "reply": "hi"}'])

    asyncio.run(server._handler(ws))

    assert server._latest == {"state": "speaking", "reply": "hi"}
    assert json.loads(ws.sent[-1]) == {"state": "speaking", "reply": "hi"}


def test_handler_ignores_malformed_json():
    server = HudServer(Config())
    ws = FakeWebSocket(["not json"])

    asyncio.run(server._handler(ws))

    assert server._latest == {"state": "idle", "transcript": "", "reply": ""}


def test_reporter_call_queues_state_payload():
    reporter = HudStateReporter(Config())
    reporter("listening", {})
    payload = reporter._queue.get(timeout=1)
    assert payload == {"state": "listening"}


def test_reporter_call_merges_extra_fields():
    reporter = HudStateReporter(Config())
    reporter("speaking", {"transcript": "hi", "reply": "hello there"})
    payload = reporter._queue.get(timeout=1)
    assert payload == {"state": "speaking", "transcript": "hi", "reply": "hello there"}


def test_handler_dispatches_request_to_api_handler(monkeypatch):
    import src.hud.api as api

    monkeypatch.setitem(api.HANDLERS, "fake_action", lambda params: {"echo": params})
    server = HudServer(Config())
    ws = FakeWebSocket([json.dumps({"type": "request", "id": "1", "action": "fake_action", "params": {"x": 1}})])

    asyncio.run(server._handler(ws))

    response = json.loads(ws.sent[-1])
    assert response == {"type": "response", "id": "1", "result": {"echo": {"x": 1}}}
    # requests must never leak into the broadcast state
    assert server._latest == {"state": "idle", "transcript": "", "reply": ""}


def test_handler_request_unknown_action_returns_error():
    server = HudServer(Config())
    ws = FakeWebSocket([json.dumps({"type": "request", "id": "1", "action": "nope"})])

    asyncio.run(server._handler(ws))

    response = json.loads(ws.sent[-1])
    assert "error" in response["result"]


def test_handler_request_handler_exception_is_caught(monkeypatch):
    import src.hud.api as api

    def boom(_params):
        raise ValueError("kaboom")

    monkeypatch.setitem(api.HANDLERS, "boom", boom)
    server = HudServer(Config())
    ws = FakeWebSocket([json.dumps({"type": "request", "id": "1", "action": "boom"})])

    asyncio.run(server._handler(ws))

    response = json.loads(ws.sent[-1])
    assert response["result"] == {"error": "kaboom"}
