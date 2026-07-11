"""Glues wake word -> recorder -> STT -> brain -> TTS into the turn-based
voice loop, and reports state transitions (idle/listening/thinking/speaking)
via a pluggable state_callback so the HUD/menu bar can reflect what Jarvis
is doing.

Deliberately has zero Cocoa/GUI code: run as `python -m src.pipeline`, this
is a plain process that only ever touches the microphone, never AppKit.
Earlier versions ran this inside the same process as the pywebview HUD
window, and it crashed the whole process the first time it opened the
mic — macOS's microphone permission/authorization flow appears to have the
same "must happen on the main thread" constraint that rumps' NSStatusBar
does, and pywebview's Cocoa run loop already owns that thread. So the mic
gets its own process, full stop; src/main.py (HUD) and src/system/menubar.py
(menu bar) are separate GUI processes that receive this process's state
updates over a WebSocket instead of a direct in-process call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.audio.recorder import Recorder
from src.audio.stt import transcribe
from src.audio.tts import speak
from src.audio.wake_word import WakeWordListener
from src.brain.agent import JarvisSession, run_turn
from src.brain.memory import seed_default_memories
from src.system.config import Config, load_config

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TRANSCRIPT_PATH = LOG_DIR / "jarvis_transcript.jsonl"

logger = logging.getLogger("jarvis")

StateCallback = Callable[[str, dict], None]


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )


def _append_transcript(transcript: str, reply: str) -> None:
    """Persists each completed turn so the React app's conversation-history
    view survives process restarts — src/pipeline.py's in-memory
    JarvisSession does not."""
    TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcript": transcript,
        "reply": reply,
    }
    with open(TRANSCRIPT_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


class VoiceConfirm:
    """Confirmation gate for voice mode: speaks the pending action out loud
    and listens for the user to say "confirm" before Jarvis proceeds."""

    def __init__(self, cfg: Config, recorder: Recorder):
        self.cfg = cfg
        self.recorder = recorder

    def __call__(self, description: str, tool_name: str, input_data: dict) -> bool:
        prompt = f"I'm about to {description}. Say confirm to proceed."
        logger.info("CONFIRM tool=%s input=%s prompt=%s", tool_name, input_data, prompt)
        speak(prompt, voice=self.cfg.voice)
        audio = self.recorder.record_utterance()
        heard = transcribe(audio, self.cfg.audio.sample_rate)
        logger.info("CONFIRM heard=%r", heard)
        return "confirm" in heard.lower()


def run_forever(
    cfg: Config | None = None,
    state_callback: StateCallback | None = None,
) -> None:
    _configure_logging()
    cfg = cfg or load_config()

    seed_default_memories()

    wake = WakeWordListener(cfg)
    recorder = Recorder(cfg)
    session = JarvisSession()
    confirm_fn = VoiceConfirm(cfg, recorder)

    def emit(state: str, **extra) -> None:
        logger.info("STATE %s %s", state, extra)
        if state_callback:
            state_callback(state, extra)

    emit("idle")
    try:
        while True:
            wake.listen_once()
            emit("listening")

            audio = recorder.record_utterance()
            emit("thinking")

            transcript = transcribe(audio, cfg.audio.sample_rate)
            if not transcript:
                logger.info("Empty transcript, returning to idle.")
                emit("idle")
                continue

            emit("thinking", transcript=transcript)
            reply = run_turn(transcript, session, cfg, confirm_fn=confirm_fn)
            _append_transcript(transcript, reply)

            emit("speaking", transcript=transcript, reply=reply)
            speak(reply, voice=cfg.voice)
            emit("idle")
    finally:
        wake.close()


if __name__ == "__main__":
    from src.hud.client import HudStateReporter

    _cfg = load_config()
    run_forever(cfg=_cfg, state_callback=HudStateReporter(_cfg))
