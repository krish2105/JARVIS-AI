"""Tests for Voice V2 primitives: the cancellation token
(src/core/cancellation.py), the voice state machine
(src/core/state_machine.py), and the barge-in cancellation checkpoint in the
agent tool loop (src/brain/agent.py). Pure Python — no audio/MLX dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.cancellation import CancellationToken, CancelledTurn  # noqa: E402
from src.core.state_machine import (  # noqa: E402
    IllegalTransition,
    VoiceState,
    VoiceStateMachine,
)

# --- cancellation ---------------------------------------------------------


def test_token_starts_uncancelled_and_flips():
    t = CancellationToken()
    assert not t.cancelled
    t.cancel()
    assert t.cancelled


def test_raise_if_cancelled():
    t = CancellationToken()
    t.raise_if_cancelled()  # no-op
    t.cancel()
    with pytest.raises(CancelledTurn):
        t.raise_if_cancelled()


# --- state machine --------------------------------------------------------


def test_legal_happy_path():
    events = []
    sm = VoiceStateMachine("s1", on_event=events.append)
    sm.transition(VoiceState.INITIALIZING)
    sm.transition(VoiceState.IDLE)
    sm.start_turn()
    sm.transition(VoiceState.WAKE_DETECTED)
    sm.transition(VoiceState.LISTENING)
    sm.transition(VoiceState.TRANSCRIBING)
    sm.transition(VoiceState.THINKING)
    sm.transition(VoiceState.SPEAKING)
    sm.transition(VoiceState.IDLE)
    assert sm.state == VoiceState.IDLE
    # seq numbers are monotonic and turn_id tracked
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[-3].turn_id == 1


def test_illegal_transition_raises():
    sm = VoiceStateMachine("s1")
    sm.transition(VoiceState.INITIALIZING)
    sm.transition(VoiceState.IDLE)
    with pytest.raises(IllegalTransition):
        sm.transition(VoiceState.SPEAKING)  # can't speak straight from idle


def test_barge_in_from_speaking_is_allowed():
    sm = VoiceStateMachine("s1")
    for s in (VoiceState.INITIALIZING, VoiceState.IDLE, VoiceState.WAKE_DETECTED,
              VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.THINKING,
              VoiceState.SPEAKING):
        sm.transition(s)
    event = sm.barge_in()
    assert event is not None
    assert sm.state == VoiceState.INTERRUPTED
    assert event.cancellable is False  # INTERRUPTED itself isn't interruptible


def test_barge_in_from_idle_is_noop():
    sm = VoiceStateMachine("s1")
    sm.transition(VoiceState.INITIALIZING)
    sm.transition(VoiceState.IDLE)
    assert sm.barge_in() is None
    assert sm.state == VoiceState.IDLE


def test_event_metadata_is_complete():
    sm = VoiceStateMachine("sess-42")
    sm.transition(VoiceState.INITIALIZING, source="test", reason="boot")
    e = sm.transition(VoiceState.IDLE, source="test", reason="ready")
    d = e.as_dict()
    assert d["session_id"] == "sess-42"
    assert d["state"] == "idle"
    assert d["previous"] == "initializing"
    assert d["source"] == "test"
    assert d["reason"] == "ready"
    assert "cancellable" in d and "seq" in d and "turn_id" in d


# --- barge-in stops the agent tool loop -----------------------------------


class _FakeLLM:
    """Always asks to run a shell command; records if chat was called."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return '{"tool_call": {"name": "run_shell", "input": {"command": "echo hi"}}}'


def test_cancelled_turn_does_not_execute_tool(monkeypatch):
    import src.brain.agent as agent
    from src.system.config import Config

    fake = _FakeLLM()
    executed = {"ran": False}

    def fake_handler(_input):
        executed["ran"] = True
        return "should not happen"

    from src.brain.tool_types import Tool
    fake_tool = Tool(name="run_shell", description="", parameters={}, handler=fake_handler)

    monkeypatch.setattr(agent, "build_tools", lambda cfg: {"run_shell": fake_tool})
    monkeypatch.setattr(agent.LocalLLM, "get", staticmethod(lambda model_id: fake))

    cancel = CancellationToken()
    cancel.cancel()  # user already barged in

    session = agent.JarvisSession()
    reply = agent.run_turn("do a thing", session, Config(), cancel=cancel)

    assert reply == agent.INTERRUPTED_REPLY
    assert executed["ran"] is False  # the stale tool must NOT have fired


def test_uncancelled_turn_still_runs(monkeypatch):
    import src.brain.agent as agent
    from src.brain.tool_types import Tool
    from src.system.config import Config

    fake = _FakeLLM()
    executed = {"ran": False}

    def fake_handler(_input):
        executed["ran"] = True
        return "done"

    fake_tool = Tool(name="run_shell", description="", parameters={}, handler=fake_handler)
    monkeypatch.setattr(agent, "build_tools", lambda cfg: {"run_shell": fake_tool})
    monkeypatch.setattr(agent.LocalLLM, "get", staticmethod(lambda model_id: fake))

    session = agent.JarvisSession()
    agent.run_turn("do a thing", session, Config(), cancel=CancellationToken())
    assert executed["ran"] is True
