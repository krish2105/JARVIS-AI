"""Tests for the P1 hardening pass: secret redaction (src/system/redaction.py),
context budgeting (src/core/context.py), and single-model residency
(src/brain/local_llm.py). All pure Python — no MLX/audio/macOS dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.local_llm import LocalLLM  # noqa: E402
from src.core.context import estimate_tokens, prune_messages  # noqa: E402
from src.system.redaction import redact_secrets  # noqa: E402

# --- redaction ------------------------------------------------------------


@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnopqrstuvwxyz012345",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
])
def test_redacts_known_token_shapes(secret):
    out = redact_secrets(f"here is the key {secret} ok")
    assert secret not in out
    assert "REDACTED" in out


def test_redacts_keyvalue_secrets():
    assert "hunter2xyz" not in redact_secrets("password=hunter2xyz")
    assert "topsecrettoken" not in redact_secrets('api_key: "topsecrettoken"')


def test_redacts_bearer_token():
    out = redact_secrets("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "eyJhbGci" not in out


def test_leaves_ordinary_text_alone():
    text = "Remind me to buy oat milk and call the dentist."
    assert redact_secrets(text) == text


# --- context budgeting ----------------------------------------------------


def _msg(role, content):
    return {"role": role, "content": content}


def test_short_history_is_unchanged():
    msgs = [_msg("system", "rules"), _msg("user", "hi"), _msg("assistant", "hello")]
    assert prune_messages(msgs, max_tokens=6000) == msgs


def test_long_history_is_pruned_but_keeps_system_and_recent():
    system = _msg("system", "SAFETY RULES")
    body = [_msg("user" if i % 2 == 0 else "assistant", "x" * 400) for i in range(40)]
    msgs = [system] + body

    pruned = prune_messages(msgs, max_tokens=1000, keep_recent=6)

    assert estimate_tokens(msgs) > 1000
    assert pruned[0] == system            # system prompt preserved
    assert pruned[-6:] == body[-6:]       # most recent turns preserved verbatim
    assert len(pruned) < len(msgs)        # something was actually dropped
    assert any("elided" in m["content"] for m in pruned)  # elision marker present


def test_summarizer_is_used_when_provided():
    system = _msg("system", "rules")
    body = [_msg("user", "y" * 400) for _ in range(30)]
    msgs = [system] + body

    pruned = prune_messages(msgs, max_tokens=500, keep_recent=4,
                            summarizer=lambda older: "SUMMARY OF THINGS")

    assert any("SUMMARY OF THINGS" in m["content"] for m in pruned)


# --- single-model residency ----------------------------------------------


def test_only_one_model_resident_at_a_time(monkeypatch):
    import src.brain.local_llm as m
    m._resident.clear()
    # Fake loader: no MLX, returns cheap placeholders.
    monkeypatch.setattr(LocalLLM, "_loader", staticmethod(lambda mid: (f"weights-{mid}", object())))

    a = LocalLLM.get("model-a")
    assert LocalLLM.resident_ids() == ["model-a"]

    b = LocalLLM.get("model-b")
    assert LocalLLM.resident_ids() == ["model-b"]   # model-a evicted
    assert b.model == "weights-model-b"

    # Same id returns the cached instance without reloading.
    assert LocalLLM.get("model-b") is b
    # A previously-evicted id is a fresh load, not the old instance.
    assert LocalLLM.get("model-a") is not a
    m._resident.clear()
