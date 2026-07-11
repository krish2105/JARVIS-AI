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
import json
import logging
import threading
from typing import Callable

import websockets

from src.system.config import Config

logger = logging.getLogger("jarvis.hud")

# Origins allowed to open the HUD socket. The legitimate clients are all
# non-browser (the voice pipeline and menu bar send no Origin header) or the
# pywebview window loaded from file:// (Origin absent or the literal "null").
# A real website the user visits sends its own https:// Origin — WebSocket
# connections are exempt from CORS, so without this check any page could open
# ws://127.0.0.1:8765 and read the transcript/memory or delete files
# (cross-site WebSocket hijacking). None means "no Origin header present".
_ALLOWED_ORIGINS = (None, "null", "file://")

# Cap inbound frames. The privileged RPC payloads are tiny; anything large is
# either a bug or an attempt to exhaust memory.
_MAX_MESSAGE_BYTES = 256 * 1024


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
                if payload.get("type") == "request":
                    await self._handle_request(websocket, payload)
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

    async def _serve_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with websockets.serve(
            self._handler,
            self.host,
            self.port,
            origins=list(_ALLOWED_ORIGINS),
            max_size=_MAX_MESSAGE_BYTES,
        ):
            logger.info("HUD websocket server listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run forever

    def run_in_background_thread(self) -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self._serve_forever()), daemon=True)
        thread.start()
        return thread
