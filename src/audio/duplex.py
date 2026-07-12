"""Experimental full-duplex speaking with echo reduction.

A single sd.Stream plays the reply AND captures the mic at the same time (two
separate streams glitch on macOS — that's the coexistence bug we hit). A
background detector subtracts a delay/gain-matched copy of what we're playing
from the mic (echo reduction — we know exactly what we output, so we can cancel
most of it) and runs the wake model on the result, so saying "Hey Jarvis" again
interrupts mid-sentence.

Off by default (config audio.full_duplex). The clean half-duplex path stays the
default. Audio quality couldn't be verified remotely — treat as beta.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque

import numpy as np
import sounddevice as sd

from src.audio.streaming_speaker import split_complete
from src.audio.tts import SAMPLE_RATE as TTS_RATE
from src.audio.tts import _get_model, _output_rate, _resample
from src.core.cancellation import CancellationToken

logger = logging.getLogger("jarvis.duplex")

_WAKE_RATE = 16000
_WAKE_FRAME = 1280


class DuplexSpeaker:
    def __init__(self, voice: str, barge_wake, cancel: CancellationToken):
        self.voice = voice
        self._wake = barge_wake            # a WakeWordListener (own model)
        self._cancel = cancel
        self._rate = _output_rate()
        self._play: deque[np.ndarray] = deque()
        self._play_lock = threading.Lock()
        self._buffer = ""
        self._synth_q: queue.Queue = queue.Queue()
        self._cap_q: queue.Queue = queue.Queue(maxsize=400)
        self._synth_done = threading.Event()
        self._stop = threading.Event()
        self.interrupted = False
        self.spoken: list[str] = []
        self._decim = max(1, round(self._rate / _WAKE_RATE))
        # echo-subtraction state
        self._echo_gain = 0.0
        self._echo_delay = 0  # in device-rate samples

        self._synth_t = threading.Thread(target=self._synth_loop, daemon=True)
        self._detect_t = threading.Thread(target=self._detect_loop, daemon=True)
        self._stream: sd.Stream | None = None

    # --- public API -------------------------------------------------------
    def start(self) -> None:
        self._stream = sd.Stream(
            samplerate=self._rate, channels=1, dtype="float32",
            blocksize=1024, callback=self._callback,
        )
        self._stream.start()
        self._synth_t.start()
        self._detect_t.start()

    def feed(self, token: str) -> None:
        """A TokenSink for run_turn: accumulate text and queue complete
        sentences for synthesis."""
        self._buffer += token
        done, self._buffer = split_complete(self._buffer)
        for s in done:
            self._synth_q.put(s)

    def finish(self, timeout: float = 30.0) -> None:
        tail = self._buffer.strip()
        self._buffer = ""
        if tail and not self._cancel.cancelled:
            self._synth_q.put(tail)
        self._synth_q.put(None)  # end of synthesis
        self._synth_t.join(timeout=timeout)
        # wait for playback to drain (or interruption)
        while not self._cancel.cancelled:
            with self._play_lock:
                empty = not self._play
            if empty and self._synth_done.is_set():
                break
            self._stop.wait(0.05)
        self._teardown()

    def _teardown(self) -> None:
        self._stop.set()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass

    # --- synthesis --------------------------------------------------------
    def _synth_loop(self) -> None:
        model = _get_model()
        try:
            while True:
                sentence = self._synth_q.get()
                if sentence is None:
                    break
                if self._cancel.cancelled:
                    continue
                self.spoken.append(sentence)
                try:
                    for chunk in model.generate(text=sentence, voice=self.voice, speed=1.0, lang_code="a"):
                        audio = np.asarray(chunk.audio, dtype=np.float32).reshape(-1)
                        audio = _resample(audio, TTS_RATE, self._rate)
                        with self._play_lock:
                            self._play.append(audio)
                except Exception:  # noqa: BLE001
                    logger.exception("duplex synth failed")
        finally:
            self._synth_done.set()

    # --- audio callback (real-time; keep light) ---------------------------
    def _callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        # output: pull queued playback samples
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._play_lock:
            while filled < frames and self._play:
                head = self._play[0]
                take = min(len(head), frames - filled)
                out[filled:filled + take] = head[:take]
                if take == len(head):
                    self._play.popleft()
                else:
                    self._play[0] = head[take:]
                filled += take
        outdata[:, 0] = out
        # capture: hand mic + the reference we just played to the detector
        try:
            self._cap_q.put_nowait((indata[:, 0].copy(), out.copy()))
        except queue.Full:
            pass

    # --- barge detector (offline; echo-reduce then run the wake model) ----
    def _detect_loop(self) -> None:
        ref_hist = np.zeros(self._rate // 2, dtype=np.float32)  # 0.5s of recent output
        mic_16k = np.zeros(0, dtype=np.float32)
        while not self._stop.is_set():
            try:
                mic, ref = self._cap_q.get(timeout=0.1)
            except queue.Empty:
                continue
            ref_hist = np.concatenate([ref_hist, ref])[-len(ref_hist):]
            self._estimate_echo(mic, ref_hist)
            cleaned = self._subtract_echo(mic, ref_hist)
            # downsample to the wake rate
            d16 = cleaned[:: self._decim].astype(np.float32)
            mic_16k = np.concatenate([mic_16k, d16])
            while len(mic_16k) >= _WAKE_FRAME:
                frame = (np.clip(mic_16k[:_WAKE_FRAME], -1, 1) * 32767).astype(np.int16)
                mic_16k = mic_16k[_WAKE_FRAME:]
                try:
                    scores = self._wake._model.predict(frame)  # noqa: SLF001
                    if self._wake._matched_score(scores) is not None:  # noqa: SLF001
                        self._wake._model.reset()  # noqa: SLF001
                        self.interrupted = True
                        self._cancel.cancel()
                        logger.info("duplex barge-in: wake word heard while speaking")
                        return
                except Exception:  # noqa: BLE001
                    logger.exception("duplex detect failed")

    def _estimate_echo(self, mic: np.ndarray, ref_hist: np.ndarray) -> None:
        # Cheap running estimate of echo delay+gain via cross-correlation on a
        # short window; only update when there is real output energy.
        if float(np.dot(ref_hist[-len(mic):], ref_hist[-len(mic):])) < 1e-4:
            return
        n = min(len(mic), 2048)
        m = mic[-n:]
        r = ref_hist[-(n + self._rate // 20):]  # search up to ~50ms of delay
        if len(r) < n:
            return
        best_lag, best_val = 0, 0.0
        for lag in range(0, len(r) - n, 32):
            seg = r[lag:lag + n]
            val = abs(float(np.dot(m, seg)))
            if val > best_val:
                best_val, best_lag = val, lag
        seg = r[best_lag:best_lag + n]
        denom = float(np.dot(seg, seg)) + 1e-6
        gain = float(np.dot(m, seg)) / denom
        # smooth
        self._echo_gain = 0.7 * self._echo_gain + 0.3 * max(0.0, min(1.5, gain))
        self._echo_delay = (len(r) - n) - best_lag

    def _subtract_echo(self, mic: np.ndarray, ref_hist: np.ndarray) -> np.ndarray:
        if self._echo_gain <= 0.01:
            return mic
        d = self._echo_delay
        ref_aligned = ref_hist[-(len(mic) + d):][:len(mic)] if len(ref_hist) >= len(mic) + d else None
        if ref_aligned is None or len(ref_aligned) != len(mic):
            return mic
        return mic - self._echo_gain * ref_aligned
