"""Mic capture with WebRTC VAD-based end-of-turn detection.

Recording starts the instant the caller invokes `record_utterance()` (i.e.
right after wake-word detection) and stops once ~`vad_silence_ms` of
trailing silence follows detected speech, or after `max_record_seconds`
as a hard cap — whichever comes first.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd
import webrtcvad

from src.system.config import Config

_FRAME_MS = 30  # webrtcvad supports 10/20/30ms frames


class Recorder:
    def __init__(self, cfg: Config, vad_aggressiveness: int = 2):
        self.sample_rate = cfg.audio.sample_rate
        self.frame_samples = int(self.sample_rate * _FRAME_MS / 1000)
        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self.silence_ms = cfg.audio.vad_silence_ms
        self.max_seconds = cfg.audio.max_record_seconds

    def record_utterance(self, on_level=None) -> np.ndarray:
        """Returns a 1-D int16 numpy array of the captured utterance. If
        `on_level` is given, it's called each frame with a 0-1 loudness value
        so the HUD can draw a live waveform of the user's voice."""
        frames: list[np.ndarray] = []
        silence_run_ms = 0
        speech_started = False
        max_frames = int((self.max_seconds * 1000) / _FRAME_MS)

        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.frame_samples,
            channels=1,
            dtype="int16",
        ) as stream:
            for _ in range(max_frames):
                pcm, _ = stream.read(self.frame_samples)
                pcm = pcm.reshape(-1)
                frames.append(pcm.copy())

                if on_level is not None:
                    rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
                    on_level(min(1.0, rms / 3000.0))  # ~3000 rms ≈ normal speech peak

                is_speech = self.vad.is_speech(pcm.tobytes(), self.sample_rate)
                if is_speech:
                    speech_started = True
                    silence_run_ms = 0
                elif speech_started:
                    silence_run_ms += _FRAME_MS
                    if silence_run_ms >= self.silence_ms:
                        break

        return np.concatenate(frames) if frames else np.array([], dtype=np.int16)
