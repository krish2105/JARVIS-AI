"""Menu bar status app for Jarvis: shows current pipeline state, lets you
pause/resume, and quit. Runs the voice pipeline in a background thread since
rumps needs the main thread for its Cocoa event loop.
"""

from __future__ import annotations

import threading

import rumps

from src.pipeline import run_forever
from src.system.config import load_config

STATE_TITLES = {
    "idle": "Jarvis \U0001f4a4",       # 💤
    "listening": "Jarvis \U0001f3a4",  # 🎤
    "thinking": "Jarvis \U0001f9e0",   # 🧠
    "speaking": "Jarvis \U0001f50a",   # 🔊
    "paused": "Jarvis ⏸",         # ⏸
}


class JarvisMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("Jarvis", title=STATE_TITLES["idle"])
        self._paused = threading.Event()
        self.pause_item = rumps.MenuItem("Pause Jarvis", callback=self._toggle_pause)
        self.menu = [self.pause_item]

        self._cfg = load_config()
        self._thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self._thread.start()

    def _run_pipeline(self) -> None:
        run_forever(
            cfg=self._cfg,
            state_callback=self._on_state,
            is_paused=self._paused.is_set,
        )

    def _on_state(self, state: str, extra: dict) -> None:
        title = STATE_TITLES["paused"] if self._paused.is_set() else STATE_TITLES.get(state, "Jarvis")
        # rumps requires UI updates to happen on the main thread; setting
        # .title directly is safe because rumps marshals it internally.
        self.title = title

    def _toggle_pause(self, sender: rumps.MenuItem) -> None:
        if self._paused.is_set():
            self._paused.clear()
            sender.title = "Pause Jarvis"
            self.title = STATE_TITLES["idle"]
        else:
            self._paused.set()
            sender.title = "Resume Jarvis"
            self.title = STATE_TITLES["paused"]


def main() -> None:
    JarvisMenuBarApp().run()


if __name__ == "__main__":
    main()
