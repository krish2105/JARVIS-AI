"""src/audio/tts.py wraps mlx-audio's Kokoro model, which only runs on Apple
Silicon with the real model downloaded. Here we stub out mlx_audio and
sounddevice so the sentence-splitting and producer/consumer streaming logic
can be verified without MLX or real audio hardware.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_stubs():
    if "sounddevice" not in sys.modules:
        sd_stub = types.ModuleType("sounddevice")
        sd_stub.play = MagicMock()
        sd_stub.wait = MagicMock()
        sd_stub.InputStream = MagicMock()
        sys.modules["sounddevice"] = sd_stub

    if "mlx_audio" not in sys.modules:
        mlx_audio_stub = types.ModuleType("mlx_audio")
        tts_stub = types.ModuleType("mlx_audio.tts")
        utils_stub = types.ModuleType("mlx_audio.tts.utils")
        utils_stub.load_model = MagicMock()
        tts_stub.utils = utils_stub
        mlx_audio_stub.tts = tts_stub
        sys.modules["mlx_audio"] = mlx_audio_stub
        sys.modules["mlx_audio.tts"] = tts_stub
        sys.modules["mlx_audio.tts.utils"] = utils_stub


_install_stubs()

from src.audio import tts  # noqa: E402


def test_split_sentences():
    text = "Hello there. How are you? I'm fine!"
    assert tts._split_sentences(text) == ["Hello there.", "How are you?", "I'm fine!"]


def test_split_sentences_empty():
    assert tts._split_sentences("   ") == []


def test_speak_noop_on_empty_text(monkeypatch):
    called = False

    def fake_get_model():
        nonlocal called
        called = True

    monkeypatch.setattr(tts, "_get_model", fake_get_model)
    tts.speak("")
    assert called is False  # nothing to synthesize, model shouldn't even load


def test_speak_streams_each_sentence_chunk(monkeypatch):
    class FakeResult:
        def __init__(self, audio):
            self.audio = audio

    class FakeModel:
        def generate(self, text, voice, speed, lang_code):
            yield FakeResult(np.zeros(10, dtype=np.float32))

    monkeypatch.setattr(tts, "_get_model", lambda: FakeModel())

    played = []
    monkeypatch.setattr(tts.sd, "play", lambda audio, samplerate: played.append(audio))
    monkeypatch.setattr(tts.sd, "wait", lambda: None)

    tts.speak("Sentence one. Sentence two.")

    assert len(played) == 2  # one chunk per sentence
