"""Plain WebSocket client that reports pipeline state to the HUD server
running in the separate GUI process (src/main.py). Used as the
`state_callback` passed to src/pipeline.py's `run_forever` when the voice
pipeline runs as its own standalone process — see that module's docstring
for why the mic and the Cocoa GUI can't share a process.

Retries silently if the HUD process isn't running (or isn't running yet) —
the voice pipeline works fine with no HUD attached; this is a best-effort
mirror, not a dependency.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading

import websockets

from src.system.config import Config

RETRY_SECONDS = 2


class HudStateReporter:
    def __init__(self, cfg: Config):
        self._url = f"ws://{cfg.hud.host}:{cfg.hud.port}"
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._run_loop, daemon=True).start()

    def __call__(self, state: str, extra: dict) -> None:
        """Matches src.pipeline's StateCallback signature."""
        self._queue.put({"state": state, **extra})

    def _run_loop(self) -> None:
        asyncio.run(self._send_forever())

    async def _send_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                async with websockets.connect(self._url) as ws:
                    while True:
                        payload = await loop.run_in_executor(None, self._queue.get)
                        await ws.send(json.dumps(payload))
            except Exception:
                await asyncio.sleep(RETRY_SECONDS)
