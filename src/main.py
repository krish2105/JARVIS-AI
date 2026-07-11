"""Jarvis entrypoint: starts the HUD websocket server, the voice pipeline,
the menu bar app, and the HUD overlay window.

Cocoa note: both rumps (menu bar) and pywebview (HUD window) want to own
macOS's main-thread run loop. pywebview's `webview.start(func, ...)` is
built for exactly this — it runs `func` in a background thread and then
blocks the *main* thread with the Cocoa GUI loop for the webview window.
Running rumps *inside* that same background thread is a known rough edge
(AppKit really prefers a single owner of the main run loop); it works in
practice for a status-item-only app but if you see menu bar flakiness on
your machine, run `python -m src.system.menubar` as its own process
instead of through this combined entrypoint, and run this file with the
HUD only.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import webview

from src.brain.memory import seed_default_memories
from src.hud.server import HudServer
from src.pipeline import run_forever
from src.system.config import load_config

WEB_DIR = Path(__file__).resolve().parent / "hud" / "web"

logger = logging.getLogger("jarvis.main")

_CORNER_OFFSETS = {
    "top-left": lambda sw, sh, w, h: (20, 20),
    "top-right": lambda sw, sh, w, h: (sw - w - 20, 20),
    "bottom-left": lambda sw, sh, w, h: (20, sh - h - 60),
    "bottom-right": lambda sw, sh, w, h: (sw - w - 20, sh - h - 60),
}


def _position_window(window: webview.Window, corner: str) -> None:
    try:
        screen = webview.screens[0]
        sw, sh = screen.width, screen.height
    except (IndexError, AttributeError):
        return
    fn = _CORNER_OFFSETS.get(corner, _CORNER_OFFSETS["bottom-right"])
    x, y = fn(sw, sh, 220, 220)
    window.move(x, y)


def _set_click_through(window: webview.Window, click_through: bool) -> None:
    """Best-effort: makes the HUD ignore mouse events while idle so it never
    steals focus/clicks from whatever app is in front. Silently no-ops if
    AppKit isn't available (e.g. non-Cocoa pywebview backend)."""
    try:
        from AppKit import NSApp  # provided by pyobjc, a pywebview[cocoa] dependency

        for ns_window in NSApp.windows():
            ns_window.setIgnoresMouseEvents_(click_through)
    except Exception:
        pass


def start_background_work(window: webview.Window, cfg, hud: HudServer) -> None:
    seed_default_memories()
    hud.run_in_background_thread()
    _position_window(window, cfg.hud.corner)

    def on_state(state: str, extra: dict) -> None:
        hud.broadcast_threadsafe(state, extra)
        _set_click_through(window, click_through=(state == "idle"))

    threading.Thread(
        target=run_forever, kwargs={"cfg": cfg, "state_callback": on_state}, daemon=True
    ).start()

    try:
        from src.system.menubar import JarvisMenuBarApp

        threading.Thread(target=lambda: JarvisMenuBarApp().run(), daemon=True).start()
    except Exception:
        logger.exception("Menu bar app failed to start; continuing with HUD only.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()

    hud = HudServer(cfg)

    window = webview.create_window(
        "Jarvis",
        url=str(WEB_DIR / "index.html"),
        width=220,
        height=220,
        frameless=True,
        easy_drag=False,
        on_top=True,
        transparent=True,
    )

    webview.start(start_background_work, (window, cfg, hud), gui="cocoa", debug=False)


if __name__ == "__main__":
    main()
