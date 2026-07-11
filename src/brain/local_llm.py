"""Loads and runs a local instruct model via mlx-lm — the free, fully
offline replacement for the Claude API reasoning step. Models are pulled
from Hugging Face on first use and cached under ~/.cache/huggingface;
after that, inference never touches the network.
"""

from __future__ import annotations

import gc
import threading

# At most ONE model stays resident at a time. Two 4-bit models (8B + 14B)
# resident together will thrash a 16 GB Mac, so switching models evicts the
# previous one and forces a collection before the next load. The cache is a
# single slot keyed by model id.
_resident: dict[str, LocalLLM] = {}
_cache_lock = threading.Lock()


def _default_loader(model_id: str):
    from mlx_lm import load

    return load(model_id)


class LocalLLM:
    # Injection points so the cache/eviction logic can be unit-tested without
    # MLX or real weights: swap _loader for a fake in tests.
    _loader = staticmethod(_default_loader)

    def __init__(self, model_id: str, max_tokens: int = 700):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.model, self.tokenizer = type(self)._loader(model_id)

    def chat(self, messages: list[dict]) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]"""
        from mlx_lm import generate

        prompt = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return generate(self.model, self.tokenizer, prompt=prompt, max_tokens=self.max_tokens, verbose=False)

    @classmethod
    def get(cls, model_id: str) -> LocalLLM:
        """Return the resident model, evicting any different one first so we
        never hold two model weight sets in memory at once."""
        with _cache_lock:
            if model_id in _resident:
                return _resident[model_id]
            # Evict whatever else is resident before loading the new weights.
            _resident.clear()
            gc.collect()
            instance = cls(model_id)
            _resident[model_id] = instance
            return instance

    @classmethod
    def resident_ids(cls) -> list[str]:
        with _cache_lock:
            return list(_resident)
