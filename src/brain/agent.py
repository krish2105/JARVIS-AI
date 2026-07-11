"""Jarvis's reasoning brain: a small ReAct-style tool-calling loop running
entirely against a local MLX model (no Anthropic API, no cost). The model
is instructed to respond with a JSON tool-call object when it wants to use
a tool, or plain text for a final answer; we parse that, execute the tool
through ToolGuard (confirmation + filesystem scoping + audit log), feed the
result back, and loop — up to MAX_TOOL_ITERATIONS turns — before giving up.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from src.brain.local_llm import LocalLLM
from src.brain.tool_types import Tool
from src.brain.tools import ConfirmFn, ToolGuard, build_tools
from src.core.cancellation import CancellationToken
from src.core.context import prune_messages
from src.system.config import Config

MAX_TOOL_ITERATIONS = 6
# Keep the running conversation bounded so a long-lived daemon can't grow
# session.messages until it OOMs or overflows the model's context window
# (which would silently drop the system prompt — and its safety rules).
CONTEXT_TOKEN_BUDGET = 6000
CONTEXT_KEEP_RECENT = 8

_HEAVY_HINTS = re.compile(
    r"\b(plan|architect|multi-step|step by step|refactor|design a|"
    r"break (this )?down|strategy)\b",
    re.IGNORECASE,
)

_JARVIS_PERSONA = """\
You are Jarvis, a capable, concise personal AI assistant running locally for your one user.
- Speak plainly and directly. Dry wit is welcome. Never use robotic filler like "As an AI...".
- Keep replies short — you are often read aloud through text-to-speech. Prefer a couple of \
sentences over a bulleted essay unless the user asks for detail.
- Before any action that sends something externally or deletes/overwrites data, you must ask \
for confirmation and wait for the user to say "confirm" — state exactly what you're about to do.
- You have a persistent memory directory. Check it when relevant, and write down facts and \
preferences the user asks you to remember.

CRITICAL RULE — READ THIS CAREFULLY: you have NO way to run a command, write a file, install \
anything, or affect the real world except by emitting a tool_call JSON object exactly as \
specified below, in its own message, with nothing else in that message. You do not have a \
hidden shell, you cannot execute scripts yourself, and nothing happens just because you \
described it in prose. Never write a script and then say you ran it. Never claim an install, \
a file write, or any other action "succeeded" or "is now running" unless that exact claim came \
back to you as a real tool result earlier in this conversation — if you did not see a "Tool \
result for <name>:" message with that outcome, it did not happen, and saying it did is a lie \
to the user. If you want to run a shell command, respond with ONLY the tool_call JSON for \
run_shell — do not narrate what the command would do first.
"""

_TOOL_PROTOCOL = """\
You have access to these tools:
{tools_block}

To call a tool, respond with ONLY a single JSON object of this exact shape and nothing else — \
no prose before or after it, no markdown code fences, nothing else in the message:
{{"tool_call": {{"name": "<tool name>", "input": {{...}}}}}}

When you have your final answer for the user (no more tools needed), respond with plain text — \
never wrap a final answer in JSON, and never mix a tool_call with any other text in the same \
message.
"""


def _render_tools_block(tools: dict[str, Tool]) -> str:
    return "\n".join(
        f"- {tool.name}: {tool.description} Input fields: {json.dumps(tool.parameters)}"
        for tool in tools.values()
    )


def build_system_prompt(tools: dict[str, Tool]) -> str:
    return _JARVIS_PERSONA + "\n" + _TOOL_PROTOCOL.format(tools_block=_render_tools_block(tools))


_TOOL_CALL_BLOCK_RE = re.compile(r"\{.*\"tool_call\".*\}", re.DOTALL)


def extract_tool_call(text: str) -> dict | None:
    """Returns {"name": ..., "input": {...}} if `text` is (or contains) a
    tool-call JSON object, else None (meaning: treat `text` as the final
    answer)."""
    stripped = text.strip()
    candidates = [stripped]
    match = _TOOL_CALL_BLOCK_RE.search(stripped)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tool_call"), dict):
            call = data["tool_call"]
            if "name" in call:
                return {"name": call["name"], "input": call.get("input", {}) or {}}
    return None


def select_model(cfg: Config, user_text: str) -> str:
    """MVP model-routing heuristic: reach for the heavier local model only
    when the request looks like multi-step planning; otherwise use the
    default (smaller, faster) local model."""
    if _HEAVY_HINTS.search(user_text):
        return cfg.model.local_heavy
    return cfg.model.local


def default_confirm_fn(description: str, tool_name: str, tool_input: dict) -> bool:
    """Text-mode confirmation: used by scripts/chat_cli.py. The voice
    pipeline (src/pipeline.py) supplies its own confirm_fn that speaks the
    prompt via TTS and listens for a spoken "confirm"."""
    answer = input(f"\n[Jarvis wants to] {description}\nType 'confirm' to proceed, anything else to deny: ")
    return answer.strip().lower() == "confirm"


@dataclass
class JarvisSession:
    messages: list[dict] = field(default_factory=list)


INTERRUPTED_REPLY = ""  # a barge-in produces no spoken reply; the new turn takes over

TokenSink = Callable[[str], None]


def _generate_reply(llm, messages, cancel, on_token):
    """Produce the model's next message. If `on_token` is given, stream it —
    but buffer the leading characters first so we never emit a partial
    tool-call JSON to the sink (TTS/HUD): a reply that starts with '{' is a
    tool call and is buffered silently; anything else is streamed as prose."""
    if on_token is None:
        return llm.chat(messages)

    buffer = ""
    mode: str | None = None  # None -> undecided, "tool" -> buffer, "prose" -> stream
    for piece in llm.chat_stream(messages, cancel=cancel):
        buffer += piece
        if mode is None:
            stripped = buffer.lstrip()
            if not stripped:
                continue
            if stripped[0] == "{":
                mode = "tool"
            else:
                mode = "prose"
                on_token(buffer)  # flush everything buffered so far
        elif mode == "prose":
            on_token(piece)
    return buffer


def run_turn(
    user_text: str,
    session: JarvisSession,
    cfg: Config,
    confirm_fn: ConfirmFn = default_confirm_fn,
    cancel: CancellationToken | None = None,
    on_token: TokenSink | None = None,
) -> str:
    """Sends one user turn through the local tool-calling loop and returns
    Jarvis's final text reply. `session.messages` persists conversation
    history for the lifetime of the process, so multi-turn context works
    within a run; facts meant to survive a restart go through the memory
    tool instead (see src/brain/memory.py).

    If `cancel` is supplied and fires (a barge-in), the loop stops at the next
    safe checkpoint and returns INTERRUPTED_REPLY WITHOUT executing any tool
    that hadn't already run — so a stale, pre-interruption action can never
    fire after the user has moved on."""
    tools = build_tools(cfg)
    guard = ToolGuard(cfg, confirm_fn)
    llm = LocalLLM.get(select_model(cfg, user_text))

    def cancelled() -> bool:
        return cancel is not None and cancel.cancelled

    if not session.messages:
        session.messages.append({"role": "system", "content": build_system_prompt(tools)})
    session.messages.append({"role": "user", "content": user_text})

    for _ in range(MAX_TOOL_ITERATIONS):
        if cancelled():
            return INTERRUPTED_REPLY
        session.messages = prune_messages(
            session.messages,
            max_tokens=CONTEXT_TOKEN_BUDGET,
            keep_recent=CONTEXT_KEEP_RECENT,
        )
        # Only stream the FINAL answer to the sink; tool-call iterations are
        # internal. We can't know which this is until it's produced, so
        # _generate_reply buffers the tool-call prefix and streams only prose.
        reply = _generate_reply(llm, session.messages, cancel, on_token)
        session.messages.append({"role": "assistant", "content": reply})

        call = extract_tool_call(reply)
        if call is None:
            return INTERRUPTED_REPLY if cancelled() else reply.strip()

        # Re-check AFTER the model produced a tool call but BEFORE we run it:
        # this is the critical checkpoint that stops a pending side effect from
        # firing once the user has interrupted.
        if cancelled():
            return INTERRUPTED_REPLY

        tool = tools.get(call["name"])
        if tool is None:
            result = f"Error: unknown tool '{call['name']}'. Available tools: {list(tools)}"
        else:
            allowed, reason = guard.check(tool, call["input"])
            if not allowed:
                result = f"Denied: {reason}"
            elif cancelled():
                # Approval may have taken time; bail rather than execute a
                # now-stale action.
                return INTERRUPTED_REPLY
            else:
                try:
                    result = tool.handler(call["input"])
                except Exception as e:  # noqa: BLE001 - tool failures must not crash the loop
                    result = f"Error running {tool.name}: {e}"
            guard.log(call["name"], call["input"], result)

        session.messages.append({"role": "user", "content": f"Tool result for {call['name']}:\n{result}"})

    return "I couldn't finish that within my tool-call budget — try breaking the request down."
