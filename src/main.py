"""Jarvis's HUD process: the WebSocket relay server plus the HUD overlay
window. Deliberately contains no microphone/pipeline code whatsoever.

Cocoa note: this process's whole job is owning pywebview's Cocoa
NSApplication run loop on the main thread. Two other things must NOT run
inside this same process:

- The menu bar (rumps also needs to own a Cocoa main-thread run loop, and
  AppKit only tolerates one owner per process — run
  `python -m src.system.menubar` as its own separate process instead).
- The voice pipeline (accessing the microphone for the first time appears
  to hit the same "must happen on the main thread" constraint that AppKit
  UI elements do; running it here crashed the entire process the instant
  it opened the mic — run `python -m src.pipeline` as its own separate
  process instead).

Both of those other processes talk to this one purely over the HUD
WebSocket server (src/hud/server.py) — the pipeline pushes state updates
in as a client (src/hud/client.py), the menu bar and this window's own
HTML/JS both consume them as clients.
"""

from __future__ import annotations

import logging
from pathlib import Path

import webview

from src.hud.server import HudServer
from src.system.config import load_config

WEB_DIR = Path(__file__).resolve().parent / "hud" / "web"

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
    hud.run_in_background_thread()
    _position_window(window, cfg.hud.corner)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()

    window: webview.Window | None = None

    def on_state(payload: dict) -> None:
        if window is not None:
            _set_click_through(window, click_through=(payload.get("state") == "idle"))

    hud = HudServer(cfg, on_state=on_state)

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
