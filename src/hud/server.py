"""WebSocket server that relays pipeline state transitions:
{"state": "idle"|"listening"|"thinking"|"speaking", "transcript": "...",
"reply": "..."} on every change.

This runs inside src/main.py (the HUD/GUI process) and acts as a small
pub-sub hub: any connected client that sends a JSON message gets it
rebroadcast to every other connected client. The voice pipeline (a
separate process — see src/pipeline.py and src/hud/client.py) pushes state
updates in as a plain WebSocket client; the HTML frontend and the menu bar
app (src/system/menubar.py) are both consumer-only clients that never send
anything. Splitting the mic-owning pipeline into its own process, with zero
Cocoa/GUI code in it, is deliberate — see src/main.py's module docstring
for why sharing a process with pywebview crashed it.
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
                await self._broadcast(payload)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

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
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info("HUD websocket server listening on ws://%s:%s", self.host, self.port)
            await asyncio.Future()  # run forever

    def run_in_background_thread(self) -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self._serve_forever()), daemon=True)
        thread.start()
        return thread
