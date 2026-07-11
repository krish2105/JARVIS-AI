"""On-device speech-to-text via MLX Whisper. No audio ever leaves the machine."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import mlx_whisper
import numpy as np

MODEL = "mlx-community/whisper-large-v3-turbo"


def _write_wav(audio: np.ndarray, sample_rate: int) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(audio.astype(np.int16).tobytes())
    return Path(tmp.name)


def transcribe(audio: np.ndarray, sample_rate: int) -> str:
    """Transcribes a 1-D int16 PCM numpy array and returns plain text."""
    if audio.size == 0:
        return ""

    wav_path = _write_wav(audio, sample_rate)
    try:
        result = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=MODEL)
    finally:
        wav_path.unlink(missing_ok=True)

    return result.get("text", "").strip()
