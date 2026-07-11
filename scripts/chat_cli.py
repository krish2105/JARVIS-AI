#!/usr/bin/env python3
"""Throwaway text-only harness for validating the brain before wiring up
audio (Phase 1 acceptance test).

Usage:
    python scripts/chat_cli.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.agent import JarvisSession, default_confirm_fn, run_turn
from src.brain.memory import seed_default_memories
from src.system.config import load_config


async def main() -> None:
    cfg = load_config()
    if not cfg.anthropic_api_key:
        print("!! ANTHROPIC_API_KEY is not set in .env — set it before chatting.")
        return

    seed_default_memories()
    session = JarvisSession()

    print("Jarvis text CLI. Ctrl-C or 'exit' to quit.\n")
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

        reply = await run_turn(user_text, session, cfg, confirm_fn=default_confirm_fn)
        print(f"jarvis> {reply}\n")


if __name__ == "__main__":
    asyncio.run(main())
