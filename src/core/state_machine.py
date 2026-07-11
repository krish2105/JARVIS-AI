"""The voice-turn state machine.

The old pipeline (src/pipeline.py) was an implicit, strictly-serial loop:
wake -> record -> think -> speak, with no way to represent "the user
interrupted", "we're waiting for approval", or "audio degraded". This makes
those states explicit and enforces legal transitions, so the HUD, the menu bar,
and the pipeline all agree on what Jarvis is doing and interruption/approval
are first-class rather than bolted on.

Pure Python and fully unit-testable — no audio/model/AppKit dependency. The
pipeline drives it; this module only models the states and their legal edges.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class VoiceState(str, Enum):
    STOPPED = "stopped"
    INITIALIZING = "initializing"
    IDLE = "idle"                    # listening for the wake word
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"          # capturing the user's utterance
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"            # model + tool loop
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"          # running an approved tool
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"      # barge-in: user spoke over Jarvis
    ERROR = "error"
    DEGRADED = "degraded"            # e.g. audio device lost, running reduced


# Legal transitions. A barge-in (-> INTERRUPTED) is allowed from any "busy"
# state; recovery (-> IDLE/ERROR/DEGRADED) is broadly allowed so the machine
# can always get back to a safe resting state.
_TRANSITIONS: dict[VoiceState, set[VoiceState]] = {
    VoiceState.STOPPED: {VoiceState.INITIALIZING},
    VoiceState.INITIALIZING: {VoiceState.IDLE, VoiceState.ERROR, VoiceState.DEGRADED, VoiceState.STOPPED},
    VoiceState.IDLE: {VoiceState.WAKE_DETECTED, VoiceState.ERROR, VoiceState.DEGRADED, VoiceState.STOPPED},
    VoiceState.WAKE_DETECTED: {VoiceState.LISTENING, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.LISTENING: {VoiceState.TRANSCRIBING, VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.TRANSCRIBING: {VoiceState.THINKING, VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.THINKING: {
        VoiceState.AWAITING_APPROVAL, VoiceState.EXECUTING, VoiceState.SPEAKING,
        VoiceState.INTERRUPTED, VoiceState.IDLE, VoiceState.ERROR,
    },
    VoiceState.AWAITING_APPROVAL: {
        VoiceState.EXECUTING, VoiceState.THINKING, VoiceState.SPEAKING,
        VoiceState.INTERRUPTED, VoiceState.IDLE, VoiceState.ERROR,
    },
    VoiceState.EXECUTING: {VoiceState.THINKING, VoiceState.SPEAKING, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.SPEAKING: {VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.INTERRUPTED: {VoiceState.LISTENING, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.ERROR: {VoiceState.IDLE, VoiceState.DEGRADED, VoiceState.STOPPED, VoiceState.INITIALIZING},
    VoiceState.DEGRADED: {VoiceState.IDLE, VoiceState.ERROR, VoiceState.STOPPED, VoiceState.INITIALIZING},
}

# States from which a barge-in is meaningful (Jarvis is doing something the
# user might want to cut off).
_INTERRUPTIBLE = {
    VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.THINKING,
    VoiceState.AWAITING_APPROVAL, VoiceState.EXECUTING, VoiceState.SPEAKING,
}


class IllegalTransition(Exception):
    pass


@dataclass
class StateEvent:
    """Emitted on every transition. Carries the metadata the master spec
    requires: session/turn ids, a monotonic sequence number, source, reason,
    and whether the new state can be cancelled."""
    session_id: str
    turn_id: int
    seq: int
    state: VoiceState
    previous: VoiceState | None
    source: str
    reason: str
    cancellable: bool
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "state": self.state.value,
            "previous": self.previous.value if self.previous else None,
            "source": self.source,
            "reason": self.reason,
            "cancellable": self.cancellable,
            **self.extra,
        }


class VoiceStateMachine:
    """Thread-safe state holder that validates transitions and emits a
    StateEvent on each one via the supplied listener."""

    def __init__(self, session_id: str, on_event: Callable[[StateEvent], None] | None = None):
        self.session_id = session_id
        self._state = VoiceState.STOPPED
        self._turn_id = 0
        self._seq = 0
        self._on_event = on_event
        self._lock = threading.RLock()

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def turn_id(self) -> int:
        return self._turn_id

    def can_transition(self, target: VoiceState) -> bool:
        return target in _TRANSITIONS.get(self._state, set())

    def start_turn(self) -> int:
        with self._lock:
            self._turn_id += 1
            return self._turn_id

    def transition(self, target: VoiceState, source: str = "pipeline", reason: str = "", **extra) -> StateEvent:
        with self._lock:
            if target != self._state and not self.can_transition(target):
                raise IllegalTransition(f"{self._state.value} -> {target.value} is not allowed")
            previous = self._state
            self._state = target
            self._seq += 1
            event = StateEvent(
                session_id=self.session_id,
                turn_id=self._turn_id,
                seq=self._seq,
                state=target,
                previous=previous,
                source=source,
                reason=reason,
                cancellable=target in _INTERRUPTIBLE,
                extra=extra,
            )
        if self._on_event:
            self._on_event(event)
        return event

    def force(self, target: VoiceState, source: str = "pipeline", reason: str = "forced") -> StateEvent:
        """Transition without validating the edge. For the live pipeline's
        defensive fallback only — a modeling mistake should degrade to a
        forced transition (logged) rather than crash the voice loop."""
        with self._lock:
            previous = self._state
            self._state = target
            self._seq += 1
            event = StateEvent(
                session_id=self.session_id,
                turn_id=self._turn_id,
                seq=self._seq,
                state=target,
                previous=previous,
                source=source,
                reason=reason,
                cancellable=target in _INTERRUPTIBLE,
            )
        if self._on_event:
            self._on_event(event)
        return event

    def barge_in(self, source: str = "wake_word", reason: str = "user spoke over Jarvis") -> StateEvent | None:
        """Transition to INTERRUPTED if the current state is interruptible;
        otherwise no-op and return None."""
        with self._lock:
            if self._state not in _INTERRUPTIBLE:
                return None
        return self.transition(VoiceState.INTERRUPTED, source=source, reason=reason)
