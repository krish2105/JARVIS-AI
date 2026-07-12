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
        self._on_command = None  # set by the pipeline to handle command-bar queries
        threading.Thread(target=self._run_loop, daemon=True).start()

    def __call__(self, state: str, extra: dict) -> None:
        """Matches src.pipeline's StateCallback signature."""
        self._queue.put({"state": state, **extra})

    def send_message(self, obj: dict) -> None:
        """Send an arbitrary control message (e.g. a command_stream chunk)."""
        self._queue.put(obj)

    def set_command_handler(self, handler) -> None:
        """handler(command_id, text) is called when the command bar submits."""
        self._on_command = handler

    def _run_loop(self) -> None:
        asyncio.run(self._send_forever())

    async def _send_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                async with websockets.connect(self._url) as ws:
                    # Full-duplex: push state out AND receive HUD button decisions.
                    await asyncio.gather(self._send_loop(ws, loop), self._recv_loop(ws))
            except Exception:
                await asyncio.sleep(RETRY_SECONDS)

    async def _send_loop(self, ws, loop) -> None:
        while True:
            payload = await loop.run_in_executor(None, self._queue.get)
            await ws.send(json.dumps(payload))

    async def _recv_loop(self, ws) -> None:
        """Resolve an approval when the user clicks Approve/Deny in the HUD."""
        from src.core.approvals import approvals

        async for message in ws:
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                continue
            if data.get("type") == "approval_decision":
                approvals.resolve(data.get("id"), bool(data.get("approved")))
            elif data.get("type") == "command" and self._on_command is not None:
                self._on_command(data.get("id"), data.get("text", ""))
