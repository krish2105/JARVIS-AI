"""src/brain/local_llm.py wraps mlx-lm, which only runs on Apple Silicon
with real model weights. Here we stub mlx_lm so the chat-template plumbing
and the process-wide model cache can be verified without MLX.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_stub():
    if "mlx_lm" in sys.modules:
        del sys.modules["mlx_lm"]
    stub = types.ModuleType("mlx_lm")
    fake_tokenizer = MagicMock()
    fake_tokenizer.apply_chat_template.return_value = "PROMPT"
    fake_model = MagicMock()
    stub.load = MagicMock(return_value=(fake_model, fake_tokenizer))
    stub.generate = MagicMock(return_value="a reply")
    sys.modules["mlx_lm"] = stub
    return stub


stub = _install_stub()

import src.brain.local_llm as local_llm  # noqa: E402


def test_get_loads_and_caches_by_model_id(monkeypatch):
    monkeypatch.setattr(local_llm, "_cache", {})
    llm1 = local_llm.LocalLLM.get("model-a")
    llm2 = local_llm.LocalLLM.get("model-a")
    assert llm1 is llm2  # cached, not reloaded
    assert stub.load.call_count == 1


def test_get_loads_separately_per_model_id(monkeypatch):
    monkeypatch.setattr(local_llm, "_cache", {})
    llm_a = local_llm.LocalLLM.get("model-a")
    llm_b = local_llm.LocalLLM.get("model-b")
    assert llm_a is not llm_b


def test_chat_applies_template_and_generates(monkeypatch):
    monkeypatch.setattr(local_llm, "_cache", {})
    llm = local_llm.LocalLLM.get("model-a")
    messages = [{"role": "user", "content": "hi"}]

    reply = llm.chat(messages)

    assert reply == "a reply"
    llm.tokenizer.apply_chat_template.assert_called_with(messages, add_generation_prompt=True)
    stub.generate.assert_called_with(llm.model, llm.tokenizer, prompt="PROMPT", max_tokens=700, verbose=False)
