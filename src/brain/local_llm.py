"""Loads and runs a local instruct model via mlx-lm — the free, fully
offline replacement for the Claude API reasoning step. Models are pulled
from Hugging Face on first use and cached under ~/.cache/huggingface;
after that, inference never touches the network.
"""

from __future__ import annotations

import threading

_cache: dict[str, "LocalLLM"] = {}
_cache_lock = threading.Lock()


class LocalLLM:
    def __init__(self, model_id: str, max_tokens: int = 700):
        from mlx_lm import load

        self.model_id = model_id
        self.max_tokens = max_tokens
        self.model, self.tokenizer = load(model_id)

    def chat(self, messages: list[dict]) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]"""
        from mlx_lm import generate

        prompt = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return generate(self.model, self.tokenizer, prompt=prompt, max_tokens=self.max_tokens, verbose=False)

    @classmethod
    def get(cls, model_id: str) -> "LocalLLM":
        """Process-wide cache so repeated turns (and both the default and
        heavy model, if both get used) don't reload weights every call."""
        with _cache_lock:
            if model_id not in _cache:
                _cache[model_id] = cls(model_id)
            return _cache[model_id]
