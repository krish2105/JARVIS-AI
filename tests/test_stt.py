"""src/audio/stt.py wraps mlx_whisper, which only runs on Apple Silicon with
the real model downloaded. Here we mock mlx_whisper itself so the wav-writing
and text-extraction logic can be verified without MLX or a GPU — the actual
"say a sentence, get a correct transcript" check is the Phase 2 acceptance
test on real hardware (see README).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub out mlx_whisper before importing src.audio.stt, since it isn't
# installable on non-Apple-Silicon machines.
if "mlx_whisper" not in sys.modules:
    stub = types.ModuleType("mlx_whisper")
    stub.transcribe = lambda *a, **k: {"text": ""}
    sys.modules["mlx_whisper"] = stub

from src.audio import stt  # noqa: E402


def test_empty_audio_returns_empty_string():
    assert stt.transcribe(np.array([], dtype=np.int16), 16000) == ""


def test_transcribe_writes_wav_and_returns_text():
    audio = (np.random.rand(16000) * 1000).astype(np.int16)  # 1s of noise @16kHz

    with patch.object(stt.mlx_whisper, "transcribe", return_value={"text": " hello jarvis "}) as mock_transcribe:
        text = stt.transcribe(audio, 16000)

    assert text == "hello jarvis"
    args, kwargs = mock_transcribe.call_args
    assert kwargs["path_or_hf_repo"] == stt.MODEL
    assert args[0].endswith(".wav")
    # the temp wav file should have been cleaned up after transcription
    assert not Path(args[0]).exists()
