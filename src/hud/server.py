"""WebSocket server that broadcasts pipeline state transitions to the HUD
frontend: {"state": "idle"|"listening"|"thinking"|"speaking", "transcript":
"...", "reply": "..."} on every change. The HUD is read-only from the
frontend's point of view — it never sends anything back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets

from src.system.config import Config

logger = logging.getLogger("jarvis.hud")


class HudServer:
    def __init__(self, cfg: Config):
        self.host = cfg.hud.host
        self.port = cfg.hud.port
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict = {"state": "idle", "transcript": "", "reply": ""}

    async def _handler(self, websocket, *_args) -> None:
        self._clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._latest))
            async for _ in websocket:
                pass  # frontend never sends anything meaningful back
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _broadcast(self, payload: dict) -> None:
        self._latest = payload
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
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info("HUD websocket server listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run forever

    def run_in_background_thread(self) -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self._serve_forever()), daemon=True)
        thread.start()
        return thread
