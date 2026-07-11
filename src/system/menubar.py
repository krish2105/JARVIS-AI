"""Menu bar status app for Jarvis: reflects the current pipeline state and
lets you quit. Deliberately does NOT run its own copy of the voice
pipeline — rumps (this app) and pywebview (the HUD window in src.main)
each require their own OS process's Cocoa main thread, and can't share
one; running the pipeline in both would also mean two processes fighting
over the same microphone. Instead this connects, as a plain WebSocket
client, to the HUD server that src.main already runs, and mirrors its
state broadcasts.

Run this alongside `python -m src.main` (or its launchd daemon), not
instead of it — this app alone has no HUD, no pipeline, no mic access.
"""

from __future__ import annotations

import asyncio
import json
import threading

import rumps
import websockets

from src.system.config import load_config

STATE_TITLES = {
    "idle": "Jarvis \U0001f4a4",       # 💤
    "listening": "Jarvis \U0001f3a4",  # 🎤
    "thinking": "Jarvis \U0001f9e0",   # 🧠
    "speaking": "Jarvis \U0001f50a",   # 🔊
}
DISCONNECTED_TITLE = "Jarvis ⚠"  # ⚠ — HUD server isn't reachable yet/anymore

RETRY_SECONDS = 2


class JarvisMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("Jarvis", title=DISCONNECTED_TITLE)
        self.menu = []
        cfg = load_config()
        self._ws_url = f"ws://{cfg.hud.host}:{cfg.hud.port}"
        threading.Thread(target=self._run_client_loop, daemon=True).start()

    def _run_client_loop(self) -> None:
        asyncio.run(self._listen_forever())

    async def _listen_forever(self) -> None:
        while True:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    async for message in ws:
                        payload = json.loads(message)
                        # rumps marshals .title assignment onto the main
                        # thread internally, so this is safe to set here.
                        self.title = STATE_TITLES.get(payload.get("state"), "Jarvis")
            except Exception:
                self.title = DISCONNECTED_TITLE
                await asyncio.sleep(RETRY_SECONDS)


def main() -> None:
    JarvisMenuBarApp().run()


if __name__ == "__main__":
    main()
