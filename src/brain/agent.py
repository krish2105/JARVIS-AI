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
from dataclasses import dataclass, field

from src.brain.local_llm import LocalLLM
from src.brain.tool_types import Tool
from src.brain.tools import ConfirmFn, ToolGuard, build_tools
from src.system.config import Config

MAX_TOOL_ITERATIONS = 6

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
"""

_TOOL_PROTOCOL = """\
You have access to these tools:
{tools_block}

To call a tool, respond with ONLY a single JSON object of this exact shape and nothing else — \
no prose before or after it:
{{"tool_call": {{"name": "<tool name>", "input": {{...}}}}}}

When you have your final answer for the user (no more tools needed), respond with plain text — \
never wrap a final answer in JSON.
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


def run_turn(
    user_text: str,
    session: JarvisSession,
    cfg: Config,
    confirm_fn: ConfirmFn = default_confirm_fn,
) -> str:
    """Sends one user turn through the local tool-calling loop and returns
    Jarvis's final text reply. `session.messages` persists conversation
    history for the lifetime of the process, so multi-turn context works
    within a run; facts meant to survive a restart go through the memory
    tool instead (see src/brain/memory.py)."""
    tools = build_tools(cfg)
    guard = ToolGuard(cfg, confirm_fn)
    llm = LocalLLM.get(select_model(cfg, user_text))

    if not session.messages:
        session.messages.append({"role": "system", "content": build_system_prompt(tools)})
    session.messages.append({"role": "user", "content": user_text})

    for _ in range(MAX_TOOL_ITERATIONS):
        reply = llm.chat(session.messages)
        session.messages.append({"role": "assistant", "content": reply})

        call = extract_tool_call(reply)
        if call is None:
            return reply.strip()

        tool = tools.get(call["name"])
        if tool is None:
            result = f"Error: unknown tool '{call['name']}'. Available tools: {list(tools)}"
        else:
            allowed, reason = guard.check(tool, call["input"])
            if not allowed:
                result = f"Denied: {reason}"
            else:
                try:
                    result = tool.handler(call["input"])
                except Exception as e:  # noqa: BLE001 - tool failures must not crash the loop
                    result = f"Error running {tool.name}: {e}"
            guard.log(call["name"], call["input"], result)

        session.messages.append({"role": "user", "content": f"Tool result for {call['name']}:\n{result}"})

    return "I couldn't finish that within my tool-call budget — try breaking the request down."
