"""Jarvis's app window process: the WebSocket relay server plus the React
app window (status ring, conversation history, memory browser, settings,
tool-activity log). Deliberately contains no microphone/pipeline code
whatsoever.

Design note — this is a normal resizable app window, not the original
spec's tiny always-on-top, click-through corner overlay: those two things
are in real tension. A frameless, click-through widget can't host a
Settings panel or a scrollable history list — you can't click a tab if
clicks pass through the window. Building out the fuller app UI meant
giving up the minimal-ambient-widget behavior; the Status tab still shows
the same animated ring, just inside a real window with a title bar.

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
React app both consume them as clients, and the React app also uses that
same connection as a request/response API (src/hud/api.py) for reading and
writing config.yaml, memories/, and the transcript/tool-call logs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import webview

from src.hud.server import HudServer
from src.system.config import load_config

WEB_DIR = Path(__file__).resolve().parent / "hud" / "web" / "dist"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()

    hud = HudServer(cfg)

    webview.create_window(
        "Jarvis",
        url=str(WEB_DIR / "index.html"),
        width=460,
        height=700,
        min_size=(360, 480),
        resizable=True,
    )

    webview.start(hud.run_in_background_thread, gui="cocoa", debug=False)


if __name__ == "__main__":
    main()
