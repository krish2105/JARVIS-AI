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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.audio.recorder import Recorder
from src.audio.stt import transcribe
from src.audio.tts import speak
from src.audio.wake_word import WakeWordListener
from src.brain.agent import JarvisSession, run_turn
from src.brain.local_llm import LocalLLM
from src.brain.memory import seed_default_memories
from src.core.cancellation import CancellationToken
from src.core.state_machine import IllegalTransition, VoiceState, VoiceStateMachine
from src.system.config import Config, load_config
from src.system.redaction import redact_secrets, rotating_handler

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TRANSCRIPT_PATH = LOG_DIR / "jarvis_transcript.jsonl"

logger = logging.getLogger("jarvis")

StateCallback = Callable[[str, dict], None]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[rotating_handler(LOG_PATH), logging.StreamHandler()],
    )


def _append_transcript(transcript: str, reply: str) -> None:
    """Persists each completed turn so the React app's conversation-history
    view survives process restarts — src/pipeline.py's in-memory
    JarvisSession does not."""
    TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcript": redact_secrets(transcript),
        "reply": redact_secrets(reply),
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


def _prewarm(cfg: Config) -> None:
    """Load the heavy models at startup so the first spoken turn isn't a
    30-60s cold start (loading Llama + Whisper + Kokoro all at once). Each is
    best-effort — a prewarm failure must not stop Jarvis from starting."""
    import numpy as np

    logger.info("prewarming models…")
    try:
        LocalLLM.get(cfg.model.local)
    except Exception:
        logger.exception("prewarm: LLM load failed")
    try:
        transcribe(np.zeros(cfg.audio.sample_rate // 2, dtype=np.int16), cfg.audio.sample_rate)
    except Exception:
        logger.exception("prewarm: STT load failed")
    try:
        from src.audio.tts import _get_model
        _get_model()
    except Exception:
        logger.exception("prewarm: TTS load failed")
    logger.info("prewarm complete")


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

    def on_event(event) -> None:
        payload = event.as_dict()
        logger.info("STATE %s", payload)
        if state_callback:
            # Backward-compatible: HUD/menu bar read payload["state"]; the
            # richer fields (turn_id, seq, reason, cancellable) ride along.
            extra = {k: v for k, v in payload.items() if k != "state"}
            state_callback(event.state.value, extra)

    sm = VoiceStateMachine(session_id="voice", on_event=on_event)

    def go(state: VoiceState, **extra) -> None:
        # Never let a transition-modeling mistake crash the live voice loop.
        try:
            sm.transition(state, source="pipeline", **extra)
        except IllegalTransition:
            logger.warning("illegal transition %s -> %s; forcing", sm.state.value, state.value)
            sm.force(state, reason="forced after illegal transition")

    go(VoiceState.INITIALIZING)
    _prewarm(cfg)  # load the heavy models NOW so the first "Hey Jarvis" is fast
    go(VoiceState.IDLE)
    try:
        while True:
            wake.listen_once()
            sm.start_turn()
            go(VoiceState.WAKE_DETECTED)
            go(VoiceState.LISTENING)

            audio = recorder.record_utterance()
            go(VoiceState.TRANSCRIBING)

            transcript = transcribe(audio, cfg.audio.sample_rate)
            if not transcript:
                logger.info("Empty transcript, returning to idle.")
                go(VoiceState.IDLE)
                continue

            # One cancellation token per turn. A barge-in watcher (mic active
            # while thinking/speaking) would call cancel.cancel(); wiring that
            # concurrent watcher is the remaining Mac-verified step — see
            # docs/IMPLEMENTATION_STATUS.md. The plumbing below already honors
            # it: run_turn won't execute a stale tool, and speak() stops audio.
            cancel = CancellationToken()

            go(VoiceState.THINKING, transcript=transcript)
            reply = run_turn(transcript, session, cfg, confirm_fn=confirm_fn, cancel=cancel)
            _append_transcript(transcript, reply)

            if not reply:  # interrupted turn produces no spoken reply
                go(VoiceState.IDLE)
                continue

            go(VoiceState.SPEAKING, transcript=transcript, reply=reply)
            interrupted = speak(reply, voice=cfg.voice, should_stop=lambda c=cancel: c.cancelled)
            if interrupted:
                sm.barge_in(source="tts", reason="playback interrupted")
            go(VoiceState.IDLE)
    finally:
        wake.close()


if __name__ == "__main__":
    from src.hud.client import HudStateReporter

    _cfg = load_config()
    run_forever(cfg=_cfg, state_callback=HudStateReporter(_cfg))
