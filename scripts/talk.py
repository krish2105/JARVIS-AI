"""Push-to-talk end-to-end voice test — NO wake word, NO launchd needed.

Run it in a Terminal that has microphone permission:

    cd ~/JARVIS-AI
    .venv/bin/python scripts/talk.py

Press Enter, speak your question, stop talking — Jarvis transcribes it, thinks,
and answers out loud. Ctrl+C to quit. This is the simplest way to confirm the
full microphone -> speech-to-text -> brain -> text-to-speech loop works, without
depending on wake-word detection or the background daemon (which macOS denies
microphone access under launchd).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio.recorder import Recorder  # noqa: E402
from src.audio.stt import transcribe  # noqa: E402
from src.audio.tts import speak  # noqa: E402
from src.brain.agent import JarvisSession, default_confirm_fn, run_turn  # noqa: E402
from src.system.config import load_config  # noqa: E402


def main() -> None:
    cfg = load_config()
    recorder = Recorder(cfg)
    session = JarvisSession()
    print("Jarvis push-to-talk. Loading models on first turn may take a minute.")
    print("Press Enter, speak your question, then stop. Ctrl+C to quit.\n")
    while True:
        try:
            input("[Press Enter to talk] ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return

        print("Listening… speak now, then pause.")
        audio = recorder.record_utterance()
        text = transcribe(audio, cfg.audio.sample_rate)
        if not text:
            print("(I didn't catch anything — try again.)\n")
            continue

        print(f"You said: {text}")
        reply = run_turn(text, session, cfg, confirm_fn=default_confirm_fn)
        print(f"Jarvis:   {reply}\n")
        if reply:
            speak(reply, voice=cfg.voice)


if __name__ == "__main__":
    main()
