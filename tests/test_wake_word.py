"""src/audio/wake_word.py wraps openWakeWord, which needs its pretrained
model files and a mic. Here we test the pure score-matching logic without
touching real hardware or downloading models — the "say Jarvis from across
the room" check is the Phase 2 acceptance test on real hardware.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_stubs():
    if "sounddevice" not in sys.modules:
        sd_stub = types.ModuleType("sounddevice")
        sd_stub.InputStream = MagicMock()
        sys.modules["sounddevice"] = sd_stub

    if "openwakeword" not in sys.modules:
        owr_stub = types.ModuleType("openwakeword")
        utils_stub = types.ModuleType("openwakeword.utils")
        utils_stub.download_models = MagicMock()
        owr_stub.utils = utils_stub
        model_module_stub = types.ModuleType("openwakeword.model")
        model_module_stub.Model = MagicMock()
        sys.modules["openwakeword"] = owr_stub
        sys.modules["openwakeword.utils"] = utils_stub
        sys.modules["openwakeword.model"] = model_module_stub


_install_stubs()

from src.audio import wake_word  # noqa: E402
from src.system.config import Config  # noqa: E402


def _make_listener(cfg: Config, model: MagicMock) -> wake_word.WakeWordListener:
    """Builds a WakeWordListener without running __init__'s model-loading
    side effects, then injects a fake model — keeps these tests fast and
    hardware-free while still exercising the real matching logic."""
    listener = wake_word.WakeWordListener.__new__(wake_word.WakeWordListener)
    listener._model = model
    listener.wake_word = cfg.wake_word.lower()
    listener.threshold = cfg.audio.wake_word_sensitivity
    listener.sample_rate = wake_word.SAMPLE_RATE
    listener.frame_length = wake_word.FRAME_LENGTH
    return listener


def test_matched_score_detects_wake_word_above_threshold():
    cfg = Config(wake_word="jarvis")
    listener = _make_listener(cfg, MagicMock())
    scores = {"hey_jarvis_v0.1": 0.8, "alexa_v0.1": 0.1}
    assert listener._matched_score(scores) == 0.8


def test_matched_score_ignores_below_threshold():
    cfg = Config(wake_word="jarvis")
    cfg.audio.wake_word_sensitivity = 0.6
    listener = _make_listener(cfg, MagicMock())
    scores = {"hey_jarvis_v0.1": 0.3}
    assert listener._matched_score(scores) is None


def test_matched_score_ignores_other_models():
    cfg = Config(wake_word="jarvis")
    listener = _make_listener(cfg, MagicMock())
    scores = {"alexa_v0.1": 0.99, "hey_mycroft_v0.1": 0.95}
    assert listener._matched_score(scores) is None


def test_listen_once_returns_on_detection_and_resets_model():
    cfg = Config(wake_word="jarvis")
    fake_model = MagicMock()
    fake_model.predict.return_value = {"hey_jarvis_v0.1": 0.9}
    listener = _make_listener(cfg, fake_model)

    fake_stream = MagicMock()
    fake_stream.read.return_value = (MagicMock(reshape=lambda *_: [0] * 1280), None)
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    wake_word.sd.InputStream = MagicMock(return_value=fake_stream)

    listener.listen_once()

    fake_model.reset.assert_called_once()
