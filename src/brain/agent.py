"""Jarvis's reasoning brain: wraps the Claude Agent SDK's query() loop with
session continuity, model routing, and a pluggable confirmation function so
both the text CLI (Phase 1) and the voice pipeline (Phase 2+) can drive it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

from src.brain.tools_config import ConfirmFn, build_options
from src.system.config import Config

_HEAVY_HINTS = re.compile(
    r"\b(plan|architect|multi-step|step by step|refactor|design a|"
    r"break (this )?down|strategy)\b",
    re.IGNORECASE,
)


def select_model(cfg: Config, user_text: str) -> str:
    """MVP model-routing heuristic: reach for the heavier model only when the
    request looks like multi-step planning; otherwise use the default model.
    (Intent triage via the fast model is left as a future optimization —
    doubling API calls per turn to save on a cheaper model isn't worth it
    for a single-user assistant.)
    """
    if _HEAVY_HINTS.search(user_text):
        return cfg.model.heavy
    return cfg.model.default


async def default_confirm_fn(description: str, tool_name: str, input_data: dict) -> bool:
    """Text-mode confirmation: used by scripts/chat_cli.py. The voice
    pipeline (src/pipeline.py) supplies its own confirm_fn that speaks the
    prompt via TTS and listens for a spoken "confirm"."""
    answer = input(f"\n[Jarvis wants to] {description}\nType 'confirm' to proceed, anything else to deny: ")
    return answer.strip().lower() == "confirm"


@dataclass
class JarvisSession:
    session_id: str | None = None


async def run_turn(
    user_text: str,
    session: JarvisSession,
    cfg: Config,
    confirm_fn: ConfirmFn = default_confirm_fn,
) -> str:
    """Sends one user turn to Claude and returns the assistant's text reply.
    Resumes the previous turn's session (if any) so context persists across
    calls within — and, since sessions are stored on disk by the SDK, even
    across restarts of — this process.
    """
    model = select_model(cfg, user_text)
    options = build_options(cfg, confirm_fn, model=model, resume=session.session_id)

    reply_parts: list[str] = []
    async for message in query(prompt=user_text, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            session.session_id = message.session_id

    return "".join(reply_parts).strip()
