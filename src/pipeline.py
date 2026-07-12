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
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from src.audio.barge_in import BargeInWatcher
from src.audio.coordinator import mic_released
from src.audio.recorder import Recorder
from src.audio.streaming_speaker import StreamingSpeaker
from src.audio.stt import transcribe
from src.audio.tts import speak
from src.audio.wake_word import WakeWordListener
from src.brain.agent import JarvisSession, run_turn
from src.brain.local_llm import LocalLLM
from src.brain.memory import seed_default_memories
from src.brain.tools import get_routine_service, set_timer_notifier
from src.core.approvals import approvals
from src.core.cancellation import CancellationToken
from src.core.state_machine import IllegalTransition, VoiceState, VoiceStateMachine
from src.system.config import Config, load_config
from src.system.notify import notify
from src.system.redaction import redact_secrets, rotating_handler

LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = LOG_DIR / "jarvis.log"
TRANSCRIPT_PATH = LOG_DIR / "jarvis_transcript.jsonl"

logger = logging.getLogger("jarvis")

StateCallback = Callable[[str, dict], None]

# MLX's Metal GPU streams are thread-local: the LLM must always generate on the
# SAME thread. A live conversation, a scheduled routine, and a command-bar query
# all run run_turn, so they're all funnelled through this single-worker pool.
_llm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-llm")


def _run_turn(*args, **kwargs):
    return _llm_pool.submit(run_turn, *args, **kwargs).result()


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

    def __init__(self, cfg: Config, recorder: Recorder, emit_state=None):
        self.cfg = cfg
        self.recorder = recorder
        self.emit_state = emit_state  # (description, tool, approval_id) -> show approval card

    def __call__(self, description: str, tool_name: str, input_data: dict) -> bool:
        approval_id = approvals.create()
        prompt = f"I'm about to {description}. Say confirm to proceed."
        logger.info("CONFIRM tool=%s input=%s id=%s", tool_name, input_data, approval_id)
        if self.emit_state:
            self.emit_state(description, tool_name, approval_id)
        speak(prompt, voice=self.cfg.voice)
        # Resolve on EITHER a spoken "confirm" or a HUD button click, whichever
        # comes first. Timeout -> denied (the safe default).
        threading.Thread(target=self._voice_resolve, args=(approval_id,), daemon=True).start()
        result = approvals.wait(approval_id, timeout=self.cfg.audio.max_record_seconds + 8)
        logger.info("CONFIRM id=%s result=%s", approval_id, result)
        return bool(result)

    def _voice_resolve(self, approval_id: str) -> None:
        try:
            audio = self.recorder.record_utterance()
            heard = transcribe(audio, self.cfg.audio.sample_rate)
            logger.info("CONFIRM heard=%r", heard)
            approvals.resolve(approval_id, "confirm" in heard.lower())
        except Exception:  # noqa: BLE001 - never leave the approval unresolved on error
            logger.exception("voice confirm failed")
            approvals.resolve(approval_id, False)


def _prewarm(cfg: Config) -> None:
    """Load the heavy models at startup so the first spoken turn isn't a
    30-60s cold start (loading Llama + Whisper + Kokoro all at once). Each is
    best-effort — a prewarm failure must not stop Jarvis from starting."""
    import numpy as np

    logger.info("prewarming models…")
    try:
        # Load on the pool thread so mlx_lm is imported there — its GPU
        # generation stream is bound to the importing thread, and ALL LLM
        # generation runs on this same pool thread (see _run_turn).
        _llm_pool.submit(LocalLLM.get, cfg.model.local).result()
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

    # One lock serializes anything that runs the model / plays audio, so a
    # timer or scheduled routine never collides with a live conversation.
    turn_lock = threading.Lock()

    def _speak_announcement(text: str, title: str) -> None:
        notify(title, text[:150])
        with mic_released():  # pause the wake-word mic so playback is clean
            speak(text, voice=cfg.voice)

    def announce_locked(text: str, title: str) -> None:
        got = turn_lock.acquire(timeout=30)  # wait for any active turn to finish
        try:
            _speak_announcement(text, title)
        finally:
            if got:
                turn_lock.release()

    # Timers announce themselves aloud + as a notification when they fire.
    set_timer_notifier(lambda msg: announce_locked(msg, "Timer"))

    def routine_trigger(prompt: str) -> bool:
        if not turn_lock.acquire(timeout=1):
            return False  # a conversation is in progress; retry on the next tick
        try:
            # Auto-deny confirmations: a routine runs unattended, so it must not
            # perform any action that would need a spoken "confirm".
            reply = _run_turn(prompt, JarvisSession(), cfg, confirm_fn=lambda *a: False)
            if reply:
                _speak_announcement(reply, "Jarvis")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("routine failed")
            return True  # don't retry a failing routine in a tight loop
        finally:
            turn_lock.release()

    routines = get_routine_service()
    routines.set_trigger(routine_trigger)
    routines.start()

    def process_command(cmd_id: str, text: str) -> None:
        """Run a typed command-bar query and stream the reply back to the HUD."""
        reporter = state_callback
        if not turn_lock.acquire(timeout=30):
            reporter.send_message({"type": "command_stream", "id": cmd_id,
                                   "reply": "I'm busy right now — try again in a moment.", "done": True})
            return
        try:
            acc = {"t": ""}

            def on_tok(tok: str) -> None:
                acc["t"] += tok
                reporter.send_message({"type": "command_stream", "id": cmd_id, "reply": acc["t"], "done": False})

            # Typed queries auto-deny confirmations (no spoken confirm loop);
            # Q&A and safe tools still work.
            reply = _run_turn(text, JarvisSession(), cfg, confirm_fn=lambda *a: False, on_token=on_tok)
            reporter.send_message({"type": "command_stream", "id": cmd_id,
                                   "reply": reply or "(no answer)", "done": True})
        except Exception:  # noqa: BLE001
            logger.exception("command failed")
            reporter.send_message({"type": "command_stream", "id": cmd_id,
                                   "reply": "Something went wrong.", "done": True})
        finally:
            turn_lock.release()

    if state_callback is not None and hasattr(state_callback, "set_command_handler"):
        state_callback.set_command_handler(
            lambda cmd_id, text: threading.Thread(
                target=process_command, args=(cmd_id, text), daemon=True
            ).start()
        )

    wake = WakeWordListener(cfg)
    # A SEPARATE wake model for barge-in. openWakeWord's model is not
    # thread-safe: the barge-in watcher runs on a background thread during a
    # turn, so it must not share `wake`'s model with the main listen loop —
    # sharing corrupts the model's rolling buffers and the wake word silently
    # stops firing after the first turn.
    barge_wake = WakeWordListener(cfg)
    recorder = Recorder(cfg)
    session = JarvisSession()

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

    def make_on_token(speaker, transcript: str, on_first=None):
        """Build the streaming sink. On the FIRST token we run `on_first` (the
        half-duplex path closes the barge-in mic here so playback is clean; the
        full-duplex path keeps it open) and flip the HUD to SPEAKING. Every
        token feeds the incremental TTS AND streams the growing reply text to
        the HUD so it types out live as it's spoken."""
        fired = {"v": False}
        acc = {"text": ""}

        def on_token(tok: str) -> None:
            acc["text"] += tok
            if not fired["v"]:
                fired["v"] = True
                if on_first is not None:
                    on_first()
                go(VoiceState.SPEAKING, transcript=transcript, reply=acc["text"])
            elif state_callback:
                # Direct partial update (not a state transition) so the reply
                # streams to the HUD without spamming the state log.
                state_callback("speaking", {"transcript": transcript, "reply": acc["text"]})
            speaker.feed(tok)

        return on_token

    # Created after `go` so a confirmation can surface an approval card in the
    # HUD (awaiting_approval) while it waits for the spoken "confirm".
    confirm_fn = VoiceConfirm(
        cfg, recorder,
        emit_state=lambda desc, tool, aid: go(
            VoiceState.AWAITING_APPROVAL, description=desc, tool=tool, approval_id=aid
        ),
    )

    go(VoiceState.INITIALIZING)
    _prewarm(cfg)  # load the heavy models NOW so the first "Hey Jarvis" is fast
    go(VoiceState.IDLE)
    try:
        while True:
            wake.listen_once()
            go(VoiceState.WAKE_DETECTED)
            audio = None  # after a barge-in, holds the user's next utterance
            turn_lock.acquire()  # block routines/timers for the whole turn
            while True:  # turn chain: a barge-in loops back WITHOUT re-waking
                sm.start_turn()
                go(VoiceState.LISTENING)
                if audio is None:
                    def on_level(lvl: float) -> None:
                        if state_callback:
                            state_callback("listening", {"level": round(lvl, 3)})
                    audio = recorder.record_utterance(on_level=on_level)
                go(VoiceState.TRANSCRIBING)

                transcript = transcribe(audio, cfg.audio.sample_rate)
                audio = None
                if not transcript:
                    logger.info("Empty transcript, returning to idle.")
                    go(VoiceState.IDLE)
                    break

                cancel = CancellationToken()
                go(VoiceState.THINKING, transcript=transcript)

                if cfg.audio.full_duplex:
                    # Full-duplex: mic stays open through playback (one duplex
                    # stream + echo reduction), so you can interrupt mid-speech.
                    from src.audio.duplex import DuplexSpeaker
                    speaker = DuplexSpeaker(cfg.voice, barge_wake, cancel)
                    speaker.start()
                    reply = _run_turn(
                        transcript, session, cfg, confirm_fn=confirm_fn,
                        cancel=cancel, on_token=make_on_token(speaker, transcript),
                    )
                    if reply and not speaker.spoken and not cancel.cancelled:
                        go(VoiceState.SPEAKING, transcript=transcript, reply=reply)
                        speaker.feed(reply)
                    speaker.finish()
                    barged = speaker.interrupted
                else:
                    # Half-duplex (default): the barge mic closes the instant
                    # speaking starts so playback has the device to itself.
                    barge = BargeInWatcher(barge_wake, cancel)
                    barge.start()
                    speaker = StreamingSpeaker(cfg.voice, cancel=cancel)
                    reply = _run_turn(
                        transcript, session, cfg, confirm_fn=confirm_fn,
                        cancel=cancel, on_token=make_on_token(speaker, transcript, on_first=barge.stop),
                    )
                    speaker.finish()
                    barge.stop()  # idempotent — already stopped when speaking began
                    if reply and not cancel.cancelled and not speaker.spoken:
                        speak(reply, voice=cfg.voice, should_stop=lambda c=cancel: c.cancelled)
                    barged = barge.triggered

                _append_transcript(transcript, reply)

                if barged or cancel.cancelled:
                    sm.barge_in(source="wake", reason="user interrupted")
                    continue  # loop: LISTENING again, capture the new command

                go(VoiceState.IDLE)
                break
            turn_lock.release()  # turn done — routines/timers may run again
    finally:
        wake.close()
        barge_wake.close()


if __name__ == "__main__":
    from src.hud.client import HudStateReporter

    _cfg = load_config()
    run_forever(cfg=_cfg, state_callback=HudStateReporter(_cfg))
