"""Process-wide registry of pending approvals.

When Jarvis needs the user to approve an action it registers a pending
approval, then blocks until it's resolved by EITHER a spoken "confirm" (the
voice path) OR a click on the HUD's Approve/Deny buttons (relayed from the HUD
process to this process's WebSocket client). First one wins; a timeout defaults
to denied — the safe outcome.
"""

from __future__ import annotations

import threading
import uuid


class ApprovalRegistry:
    def __init__(self):
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        approval_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._pending[approval_id] = {"event": threading.Event(), "result": None}
        return approval_id

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """Record a decision. First decision wins; a later one is ignored.
        Returns True if this call is the one that decided it."""
        with self._lock:
            entry = self._pending.get(approval_id)
            if entry is None or entry["event"].is_set():
                return False
            entry["result"] = bool(approved)
            entry["event"].set()
        return True

    def wait(self, approval_id: str, timeout: float) -> bool | None:
        """Block until resolved or timeout. Returns the decision, or None on
        timeout. Removes the entry either way."""
        with self._lock:
            entry = self._pending.get(approval_id)
        if entry is None:
            return None
        got = entry["event"].wait(timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        return entry["result"] if got else None


# One registry for the whole process (the voice pipeline).
approvals = ApprovalRegistry()
