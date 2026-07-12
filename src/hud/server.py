"""WebSocket server with two jobs, multiplexed on one connection:

1. Pub-sub relay: any connected client that sends a message without a
   "type" of "request" gets it rebroadcast to every other connected
   client — e.g. {"state": "listening"} from the voice pipeline process
   (src/pipeline.py, src/hud/client.py) reaches the React frontend and
   the menu bar app (src/system/menubar.py), both consumer-only clients
   that never send state themselves.
2. Request/response API: a client sends {"type": "request", "id": ...,
   "action": "...", "params": {...}}; the server answers only that
   client with {"type": "response", "id": ..., "result": {...}}, backed
   by the handlers in src/hud/api.py. This is how the React app reads/
   writes config.yaml, memories/, and the transcript/tool-call logs —
   all of which this process (src/main.py) already has filesystem access
   to, with no need to route through the mic-owning pipeline process.

Splitting the mic-owning pipeline into its own process, with zero Cocoa/
GUI code in it, is deliberate — see src/main.py's module docstring for why
sharing a process with pywebview crashed it.
"""

from __future__ import annotations

import asyncio
import http
import json
import logging
import threading
from collections.abc import Callable
from urllib.parse import urlparse

import websockets

from src.system.config import Config

logger = logging.getLogger("jarvis.hud")

# Hosts that only a LOCAL document can present. A remote website (the CSWSH
# threat) always sends its own domain as the Origin — it can never forge a
# localhost origin — so trusting these keeps the attack closed while letting
# the HUD's own frontend connect regardless of how pywebview serves it.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _origin_allowed(origin: str | None) -> bool:
    """Which WebSocket Origins may open the HUD socket.

    Allowed: no Origin header (native clients — the voice pipeline and menu
    bar send none), a file:// document, and any locally-served document
    (the pywebview HUD window). Rejected: a real remote website's https://
    Origin — that is the cross-site WebSocket hijacking case that would
    otherwise read the transcript/memory or delete files.
    """
    if origin in (None, "", "null", "file://"):
        return True
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTS

# Cap inbound frames. The privileged RPC payloads are tiny; anything large is
# either a bug or an attempt to exhaust memory.
_MAX_MESSAGE_BYTES = 256 * 1024


class _DropHandshakeRejections(logging.Filter):
    """websockets logs every rejected opening handshake at ERROR with a full
    traceback — including the Origin rejections our allowlist is *supposed* to
    produce (a web page probing :8765 is turned away, exactly as designed).
    Those are expected and benign, so we drop them to keep jarvis.log clean.
    Genuine server errors carry a different message and still get through."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "opening handshake failed" not in record.getMessage()


def _quiet_handshake_rejections() -> None:
    """Attach the drop filter to the websockets server logger, once."""
    ws_logger = logging.getLogger("websockets.server")
    if not any(isinstance(f, _DropHandshakeRejections) for f in ws_logger.filters):
        ws_logger.addFilter(_DropHandshakeRejections())


class HudServer:
    def __init__(self, cfg: Config, on_state: Callable[[dict], None] | None = None):
        """on_state, if given, fires (on the server's event-loop thread)
        every time any state payload is broadcast — whether it came from a
        remote client (the voice pipeline process) or a local
        broadcast_threadsafe() call. src/main.py uses this to drive the
        HUD window's click-through behavior."""
        self.host = cfg.hud.host
        self.port = cfg.hud.port
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict = {"state": "idle", "transcript": "", "reply": ""}
        self._on_state = on_state

    async def _handler(self, websocket, *_args) -> None:
        self._clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._latest))
            async for message in websocket:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                ptype = payload.get("type")
                if ptype == "request":
                    await self._handle_request(websocket, payload)
                elif ptype:
                    # Any typed control message (approval_decision, command,
                    # command_stream) is relayed between clients WITHOUT being
                    # treated as a status update.
                    await self._relay(payload)
                else:
                    await self._broadcast(payload)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _handle_request(self, websocket, payload: dict) -> None:
        from src.hud import api  # local import: keeps api.py's own imports

        action = payload.get("action")
        handler = api.HANDLERS.get(action)
        if handler is None:
            result = {"error": f"unknown action '{action}'"}
        else:
            try:
                result = handler(payload.get("params", {}) or {})
            except Exception as e:  # noqa: BLE001 - a bad request must not kill the connection
                result = {"error": str(e)}
        response = {"type": "response", "id": payload.get("id"), "result": result}
        try:
            await websocket.send(json.dumps(response))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _relay(self, payload: dict) -> None:
        """Send a message to all clients WITHOUT recording it as the latest
        state (used for approval decisions travelling HUD -> pipeline)."""
        if not self._clients:
            return
        message = json.dumps(payload)
        await asyncio.gather(
            *(client.send(message) for client in list(self._clients)),
            return_exceptions=True,
        )

    async def _broadcast(self, payload: dict) -> None:
        self._latest = payload
        if self._on_state:
            self._on_state(payload)
        if not self._clients:
            return
        message = json.dumps(payload)
        await asyncio.gather(
            *(client.send(message) for client in list(self._clients)),
            return_exceptions=True,
        )

    def broadcast_threadsafe(self, state: str, extra: dict) -> None:
        """Matches src.pipeline's StateCallback signature — pass this
        directly as `state_callback` to `run_forever`."""
        payload = {"state": state, **extra}
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)
        else:
            self._latest = payload

    def _process_request(self, connection, request):
        """Pre-handshake Origin gate. Rejecting here (rather than via the
        static `origins=` list) lets us trust any localhost-served frontend
        while still refusing remote origins, and logs what was refused."""
        origin = request.headers.get("Origin")
        if _origin_allowed(origin):
            return None
        logger.info("HUD: rejected WebSocket from origin=%r", origin)
        return connection.respond(http.HTTPStatus.FORBIDDEN, "origin not allowed\n")

    async def _serve_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        _quiet_handshake_rejections()
        async with websockets.serve(
            self._handler,
            self.host,
            self.port,
            process_request=self._process_request,
            max_size=_MAX_MESSAGE_BYTES,
        ):
            logger.info("HUD websocket server listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run forever

    def run_in_background_thread(self) -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self._serve_forever()), daemon=True)
        thread.start()
        return thread
