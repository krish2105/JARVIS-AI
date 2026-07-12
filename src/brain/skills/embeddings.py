"""Local sentence embeddings for semantic RAG.

Runs a small MiniLM sentence-transformer as ONNX through onnxruntime — the same
runtime already used for the wake word — with the tokenizer from `transformers`
and the model file pulled from the Hugging Face hub on first use (like every
other model in this project). No torch, no cloud.

Everything here is BEST EFFORT: if the model can't be downloaded or loaded (no
network on first run, etc.), `Embedder.available()` returns False and the RAG
layer falls back to BM25 lexical search. Semantic search never becomes a hard
dependency for retrieval to work.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

logger = logging.getLogger("jarvis.embeddings")

# Xenova's ONNX export of sentence-transformers/all-MiniLM-L6-v2: a standard
# BERT encoder (384-dim), ~90MB, ships tokenizer.json for a fast tokenizer.
_REPO = "Xenova/all-MiniLM-L6-v2"
_ONNX_FILE = "onnx/model.onnx"
DIM = 384
_MAX_TOKENS = 256


class Embedder:
    def __init__(self, repo: str = _REPO):
        self.repo = repo
        self._sess = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._loaded = False
        self._failed = False
        self._lock = threading.Lock()

    def _load(self) -> bool:
        if self._loaded:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._loaded:
                return True
            if self._failed:
                return False
            try:
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
                from transformers import AutoTokenizer

                model_path = hf_hub_download(self.repo, _ONNX_FILE)
                self._sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
                self._input_names = {i.name for i in self._sess.get_inputs()}
                self._tokenizer = AutoTokenizer.from_pretrained(self.repo)
                self._loaded = True
                logger.info("embedder ready (%s)", self.repo)
                return True
            except Exception:  # noqa: BLE001 - any failure → fall back to BM25
                logger.exception("embedder unavailable; falling back to lexical search")
                self._failed = True
                return False

    def available(self) -> bool:
        return self._load()

    def embed(self, texts: list[str]) -> np.ndarray | None:
        """Return L2-normalized embeddings [N, DIM], or None if unavailable."""
        if not texts or not self._load():
            return None
        try:
            enc = self._tokenizer(
                texts, padding=True, truncation=True, max_length=_MAX_TOKENS, return_tensors="np"
            )
            feed = {k: v for k, v in enc.items() if k in self._input_names}
            # BERT wants token_type_ids; synthesize zeros if the tokenizer omitted them.
            if "token_type_ids" in self._input_names and "token_type_ids" not in feed:
                feed["token_type_ids"] = np.zeros_like(enc["input_ids"])
            last_hidden = self._sess.run(None, feed)[0]  # [N, T, DIM]
            mask = enc["attention_mask"].astype(np.float32)[..., None]  # [N, T, 1]
            summed = (last_hidden * mask).sum(axis=1)
            counts = np.clip(mask.sum(axis=1), 1e-9, None)
            emb = summed / counts
            norms = np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9, None)
            return (emb / norms).astype(np.float32)
        except Exception:  # noqa: BLE001
            logger.exception("embedding failed")
            return None


_embedder: Embedder | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = Embedder()
        return _embedder
