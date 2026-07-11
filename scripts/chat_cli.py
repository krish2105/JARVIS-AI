#!/usr/bin/env python3
"""Throwaway text-only harness for validating the brain before wiring up
audio (Phase 1 acceptance test). Runs entirely on the local MLX model —
first run downloads it from Hugging Face (a few GB), then it's offline.

Usage:
    python scripts/chat_cli.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.agent import JarvisSession, default_confirm_fn, run_turn
from src.brain.memory import seed_default_memories
from src.system.config import load_config


def main() -> None:
    cfg = load_config()
    seed_default_memories()
    session = JarvisSession()

    print("Jarvis text CLI (local model). Ctrl-C or 'exit' to quit.")
    print(f"Model: {cfg.model.local} (first message will download it if not cached)\n")
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            break

        reply = run_turn(user_text, session, cfg, confirm_fn=default_confirm_fn)
        print(f"jarvis> {reply}\n")


if __name__ == "__main__":
    main()
