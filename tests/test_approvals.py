"""Tests for the approval registry (src/core/approvals.py) — the coordination
point where a spoken 'confirm' OR a HUD button click resolves a pending action.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.approvals import ApprovalRegistry, approvals  # noqa: E402
from src.hud.server import HudServer  # noqa: E402
from src.system.config import Config  # noqa: E402


def test_resolve_before_wait_returns_decision():
    reg = ApprovalRegistry()
    aid = reg.create()
    reg.resolve(aid, True)
    assert reg.wait(aid, timeout=1) is True


def test_resolve_from_another_thread():
    reg = ApprovalRegistry()
    aid = reg.create()

    def approve_soon():
        time.sleep(0.05)
        reg.resolve(aid, True)

    threading.Thread(target=approve_soon, daemon=True).start()
    assert reg.wait(aid, timeout=2) is True


def test_timeout_returns_none():
    reg = ApprovalRegistry()
    aid = reg.create()
    assert reg.wait(aid, timeout=0.1) is None


def test_deny_decision():
    reg = ApprovalRegistry()
    aid = reg.create()
    reg.resolve(aid, False)
    assert reg.wait(aid, timeout=1) is False


def test_first_decision_wins():
    reg = ApprovalRegistry()
    aid = reg.create()
    assert reg.resolve(aid, True) is True
    # a second resolve after the first is a no-op on an already-set result
    reg.resolve(aid, False)
    assert reg.wait(aid, timeout=1) is True


def test_resolve_unknown_id():
    reg = ApprovalRegistry()
    assert reg.resolve("nope", True) is False


def test_wait_removes_entry():
    reg = ApprovalRegistry()
    aid = reg.create()
    reg.resolve(aid, True)
    assert reg.wait(aid, timeout=1) is True
    # entry gone: a second wait finds nothing
    assert reg.wait(aid, timeout=0.1) is None


def test_hud_button_relays_through_server_to_pipeline():
    """End-to-end: a HUD client's approval_decision is relayed by the server to
    the pipeline client, resolves the approval, and does NOT overwrite the
    broadcast status state."""
    async def scenario():
        server = HudServer(Config())
        server._loop = asyncio.get_running_loop()
        async with websockets.serve(server._handler, "127.0.0.1", 0) as srv:
            url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
            aid = approvals.create()

            async def pipeline_client():
                async with websockets.connect(url, open_timeout=3) as ws:
                    await ws.recv()  # initial state
                    async for msg in ws:
                        d = json.loads(msg)
                        if d.get("type") == "approval_decision":
                            approvals.resolve(d["id"], bool(d["approved"]))
                            return

            task = asyncio.ensure_future(pipeline_client())
            await asyncio.sleep(0.2)
            async with websockets.connect(url, open_timeout=3) as hud:
                await hud.recv()
                await hud.send(json.dumps({"type": "approval_decision", "id": aid, "approved": True}))
            await asyncio.wait_for(task, timeout=3)
            return approvals.wait(aid, timeout=1), server._latest.get("type")

    resolved, latest_type = asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    assert resolved is True
    assert latest_type != "approval_decision"  # status not polluted by the relay
