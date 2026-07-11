"""Shared `Tool` definition used by the local tool-calling loop (src/brain/agent.py).

Kept in its own module (rather than inside tools.py) purely to avoid an
import cycle between tools.py and browser_tools.py, both of which need it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON-schema-ish "properties" block, rendered into the system prompt
    handler: Callable[[dict], str]
    # Key into config.yaml's require_confirmation_for that gates this tool, or None.
    confirm_key: str | None = None
