"""src/audio/wake_word.py wraps pvporcupine, which needs a real Picovoice
access key and a mic. Here we test the pure keyword-resolution logic and the
access-key guard without touching real hardware — the "say Jarvis from
across the room" check is the Phase 2 acceptance test on real hardware.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_stubs():
    if "pvporcupine" not in sys.modules:
        stub = types.ModuleType("pvporcupine")
        stub.create = MagicMock()
        sys.modules["pvporcupine"] = stub
    if "sounddevice" not in sys.modules:
        sd_stub = types.ModuleType("sounddevice")
        sd_stub.InputStream = MagicMock()
        sys.modules["sounddevice"] = sd_stub


_install_stubs()

from src.audio import wake_word  # noqa: E402
from src.system.config import Config  # noqa: E402


def test_stock_keyword_resolves_to_keywords_arg():
    cfg = Config(wake_word="jarvis")
    assert wake_word._keyword_args(cfg) == {"keywords": ["jarvis"]}


def test_custom_model_path_takes_precedence():
    cfg = Config(wake_word="jarvis", wake_word_model_path="/path/to/custom.ppn")
    assert wake_word._keyword_args(cfg) == {"keyword_paths": ["/path/to/custom.ppn"]}


def test_unknown_keyword_without_model_path_raises():
    cfg = Config(wake_word="not-a-real-keyword")
    with pytest.raises(ValueError, match="not a stock Porcupine keyword"):
        wake_word._keyword_args(cfg)


def test_listener_requires_access_key():
    cfg = Config(wake_word="jarvis", picovoice_access_key=None)
    with pytest.raises(RuntimeError, match="PICOVOICE_ACCESS_KEY"):
        wake_word.WakeWordListener(cfg)


def test_listener_creates_porcupine_with_access_key(monkeypatch):
    cfg = Config(wake_word="jarvis", picovoice_access_key="fake-key")
    fake_porcupine = MagicMock(sample_rate=16000, frame_length=512)
    monkeypatch.setattr(wake_word.pvporcupine, "create", lambda **kwargs: fake_porcupine)

    listener = wake_word.WakeWordListener(cfg)

    assert listener.sample_rate == 16000
    assert listener.frame_length == 512
