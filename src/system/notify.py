"""macOS notification banners via osascript. No dependency, no setup.

Text is passed through osascript's argv, never interpolated into the script, so
a notification body can't inject AppleScript.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("jarvis.notify")


def notify(title: str, message: str) -> None:
    try:
        subprocess.run(
            [
                "osascript",
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run",
                title, message,
            ],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:  # noqa: BLE001 - a notification must never break the caller
        logger.exception("notification failed")
