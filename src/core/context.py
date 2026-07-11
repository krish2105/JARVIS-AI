"""Conversation-context budgeting.

The agent loop (src/brain/agent.py) used to append to `session.messages`
forever — every turn, every tool call and result. Over a long-running daemon
that grows without bound: memory climbs and eventually the prompt exceeds the
model's context window and generation fails or silently truncates the system
prompt (losing the safety rules).

`prune_messages` keeps the conversation within a token budget by preserving
the system prompt and the most recent turns verbatim, and — if a summarizer is
supplied — collapsing the older middle into a single summary message. Token
counts are estimated (no tokenizer dependency here) so this stays pure Python
and unit-testable without MLX.
"""

from __future__ import annotations

from collections.abc import Callable

# Rough chars-per-token for English + JSON tool payloads. Intentionally
# conservative (small divisor => higher estimate => prune sooner).
_CHARS_PER_TOKEN = 3.5

Summarizer = Callable[[list[dict]], str]


def estimate_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return int(total_chars / _CHARS_PER_TOKEN)


def prune_messages(
    messages: list[dict],
    max_tokens: int = 6000,
    keep_recent: int = 8,
    summarizer: Summarizer | None = None,
) -> list[dict]:
    """Return a pruned copy of `messages` fitting within `max_tokens`.

    Always preserves a leading system message. Keeps the last `keep_recent`
    messages verbatim. If the total still exceeds budget, the middle is either
    summarized (when `summarizer` is given) or dropped, with a marker so the
    model knows history was elided.
    """
    if estimate_tokens(messages) <= max_tokens:
        return list(messages)

    system: list[dict] = []
    body = list(messages)
    if body and body[0].get("role") == "system":
        system = [body[0]]
        body = body[1:]

    if len(body) <= keep_recent:
        # Nothing safe to drop; return as-is rather than losing recent turns.
        return system + body

    recent = body[-keep_recent:]
    older = body[:-keep_recent]

    if summarizer is not None and older:
        try:
            summary_text = summarizer(older)
        except Exception:  # noqa: BLE001 - summarization failure must not break the turn
            summary_text = None
        if summary_text:
            marker = {"role": "system", "content": f"[Summary of earlier conversation]\n{summary_text}"}
            return system + [marker] + recent

    marker = {"role": "system", "content": f"[{len(older)} earlier messages elided to fit context]"}
    return system + [marker] + recent
