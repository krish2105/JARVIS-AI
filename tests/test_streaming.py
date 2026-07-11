"""Tests for streaming generation (src/brain/local_llm.py chat_stream +
src/brain/agent.py _generate_reply / run_turn on_token). Pure Python — a fake
streaming LLM stands in for MLX.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.brain.agent as agent  # noqa: E402
from src.brain.tool_types import Tool  # noqa: E402
from src.core.cancellation import CancellationToken  # noqa: E402
from src.system.config import Config  # noqa: E402


class _StreamLLM:
    """Yields pre-scripted pieces per turn from chat_stream."""

    def __init__(self, turns: list[list[str]]):
        self.turns = turns
        self.i = 0

    def chat_stream(self, messages, cancel=None):
        pieces = self.turns[self.i]
        self.i += 1
        for p in pieces:
            if cancel is not None and cancel.cancelled:
                return
            yield p

    def chat(self, messages):
        pieces = self.turns[self.i]
        self.i += 1
        return "".join(pieces)


def _use_llm(monkeypatch, fake, tools=None):
    monkeypatch.setattr(agent.LocalLLM, "get", staticmethod(lambda model_id: fake))
    monkeypatch.setattr(agent, "build_tools", lambda cfg: tools or {})


def test_prose_reply_is_streamed(monkeypatch):
    fake = _StreamLLM([["Hel", "lo ", "there."]])
    _use_llm(monkeypatch, fake)
    tokens: list[str] = []
    reply = agent.run_turn("hi", agent.JarvisSession(), Config(), on_token=tokens.append)
    assert reply == "Hello there."
    assert "".join(tokens) == "Hello there."


def test_tool_call_json_is_not_streamed_to_sink(monkeypatch):
    ran = {"n": 0}

    def handler(_input):
        ran["n"] += 1
        return "42"

    tool = Tool(name="calc", description="", parameters={}, handler=handler)
    tool_json = '{"tool_call": {"name": "calc", "input": {}}}'
    # First turn: the tool-call JSON, streamed char-by-char. Second: the answer.
    fake = _StreamLLM([list(tool_json), ["The ", "answer ", "is 42."]])
    _use_llm(monkeypatch, fake, tools={"calc": tool})

    tokens: list[str] = []
    reply = agent.run_turn("compute", agent.JarvisSession(), Config(), on_token=tokens.append)

    assert ran["n"] == 1                      # the tool actually ran
    assert reply == "The answer is 42."
    streamed = "".join(tokens)
    assert streamed == "The answer is 42."    # only prose reached the sink
    assert "tool_call" not in streamed        # the JSON never leaked to TTS/HUD


def test_stream_stops_on_cancel(monkeypatch):
    fake = _StreamLLM([["one ", "two ", "three"]])
    _use_llm(monkeypatch, fake)
    cancel = CancellationToken()
    cancel.cancel()  # already interrupted
    tokens: list[str] = []
    reply = agent.run_turn("hi", agent.JarvisSession(), Config(), cancel=cancel, on_token=tokens.append)
    # Cancelled before the loop body runs => interrupted, nothing streamed.
    assert reply == agent.INTERRUPTED_REPLY
    assert tokens == []
