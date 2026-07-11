"""Tests for incremental TTS (src/audio/streaming_speaker.py) and barge-in
(src/audio/barge_in.py). Injected fakes stand in for real audio, so these run
without a microphone or MLX.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio.barge_in import BargeInWatcher  # noqa: E402
from src.audio.streaming_speaker import StreamingSpeaker, split_complete  # noqa: E402
from src.core.cancellation import CancellationToken  # noqa: E402

# --- sentence segmentation ------------------------------------------------


def test_split_complete_peels_finished_sentences():
    assert split_complete("Hello there. How") == (["Hello there."], "How")
    assert split_complete("no terminator yet") == ([], "no terminator yet")
    assert split_complete("One. Two! Three? tail") == (["One.", "Two!", "Three?"], "tail")


def test_split_complete_keeps_decimals_intact():
    # "3.5" must not be treated as a sentence boundary (no space after the dot).
    assert split_complete("It is 3.5 meters wide") == ([], "It is 3.5 meters wide")


# --- incremental TTS ------------------------------------------------------


def test_speaks_each_sentence_as_it_streams():
    spoken: list[str] = []
    sp = StreamingSpeaker("am_liam", speak_fn=lambda t, v, s: spoken.append(t))
    for tok in ["Hel", "lo ", "there. ", "How are ", "you? ", "All good."]:
        sp.feed(tok)
    sp.finish(timeout=5)
    assert spoken == ["Hello there.", "How are you?", "All good."]


def test_final_partial_sentence_is_flushed_on_finish():
    spoken: list[str] = []
    sp = StreamingSpeaker("v", speak_fn=lambda t, v, s: spoken.append(t))
    sp.feed("Just one line with no period")
    sp.finish(timeout=5)
    assert spoken == ["Just one line with no period"]


def test_cancel_stops_remaining_sentences():
    spoken: list[str] = []
    cancel = CancellationToken()

    def fake_speak(text, voice, should_stop):
        spoken.append(text)
        cancel.cancel()  # barge-in right after the first sentence

    sp = StreamingSpeaker("v", cancel=cancel, speak_fn=fake_speak)
    sp.feed("One. Two. Three. ")
    sp.finish(timeout=5)
    assert spoken == ["One."]  # the rest were drained, not spoken


# --- barge-in -------------------------------------------------------------


class _FakeWake:
    def __init__(self, will_detect: bool):
        self._detect = will_detect

    def detect_until(self, stop_event) -> bool:
        if self._detect:
            return True
        stop_event.wait()  # block until told to stop, like the real mic loop
        return False


def test_barge_in_fires_cancel_when_wake_heard():
    cancel = CancellationToken()
    w = BargeInWatcher(_FakeWake(True), cancel)
    w.start()
    time.sleep(0.1)
    w.stop()
    assert w.triggered is True
    assert cancel.cancelled is True


def test_no_barge_in_when_silent():
    cancel = CancellationToken()
    w = BargeInWatcher(_FakeWake(False), cancel)
    w.start()
    time.sleep(0.1)
    w.stop()
    assert w.triggered is False
    assert cancel.cancelled is False
