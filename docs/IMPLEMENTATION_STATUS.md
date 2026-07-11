# Implementation Status

_Resumable state so the next session continues without redoing the audit._

Branch: `claude/jarvis-v2-security-hardening` (baseline preserved on `main`).
Verification env: Python 3.9 venv (project targets 3.12); no Apple-Silicon MLX,
no audio hardware, no Xcode, no signing identity.

## Commits so far

1. `chore: baseline snapshot` — pre-change snapshot (git was not initialized).
2. `fix(security): P0 hardening` — shell allowlist, browser gating, WS Origin,
   config validation, PII removal.
3. `feat(p1): bounded context...` — context pruning, single-model residency,
   redaction+rotation, CI/ruff/bandit.
4. `feat(voice-v2): state machine...` — VoiceState machine, cancellation,
   interruptible TTS, barge-in plumbing.
5. `feat(mac): native scaffold + docs` — SwiftUI menu-bar scaffold + these docs.

## Verified in this environment ✅

- 87 hardware-independent tests pass (run split to avoid a hang caused by the
  user's *live* HUD server on :8765 interacting with the reporter-thread tests).
- `ruff check src tests` clean; `bandit -c pyproject.toml -r src` clean.
- CSWSH Origin rejection verified live against websockets 15.
- run_shell metacharacter/allowlist rejection verified.

## NOT verified (needs your Mac) ❌

Wake word, STT, TTS, MLX brain, the full voice loop, the pywebview HUD, the
menu bar, launchd, the Swift app (uncompiled), any latency/memory/soak
benchmark, signing/notarization.

## Done

- [x] P0-1 arbitrary shell → allowlisted, shell=False, scrubbed env
- [x] P0-2 browser tools → mutating actions confirmation-gated
- [x] P0-3 WebSocket CSWSH → Origin allowlist + message cap
- [x] P0-4 settings could disable safety → allowlist + confirmation validation
- [x] P0-5 committed PII → untracked + de-personalized
- [x] P1 bounded context + summarizer hook
- [x] P1 single-model residency (evict before load)
- [x] P1 log/transcript redaction + rotation
- [x] P1 CI + ruff + bandit + pip-audit config
- [x] Voice V2 state machine + cancellation + interruptible TTS (logic)
- [x] Native menu-bar scaffold + login item + worker supervisor (uncompiled)

## Next actions (in order)

1. **Concurrent barge-in watcher** — a mic stream active during THINKING/
   SPEAKING that calls `cancel.cancel()` on detected speech/wake. Python
   plumbing already honors it (`run_turn` cancel checkpoints, `speak`
   `should_stop`). Needs Mac audio verification. Target: barge-in stop < 250 ms.
2. **Official MCP SDK** — replace `src/brain/mcp_client.py` with the `mcp`
   Python SDK; add child-process supervision + restart for @playwright/mcp.
3. **Streaming generation** — `LocalLLM.chat_stream` via `mlx_lm.stream_generate`;
   stream tokens to HUD and start TTS on first sentence; wire cancel into the
   stream loop.
4. **Structured memory** — SQLite store (provenance/confidence/retention) +
   migration from `memories/*.md`; keep the memory tool interface.
5. **Embedded-runtime packaging** — bundle Python + venv into the .app so
   `WorkerSupervisor` paths resolve; then sign + notarize (needs Developer ID).
6. **Fill Settings/Permissions/Memory/History/Diagnostics** surfaces in the app.
7. **model pins** — pin MLX model revisions (currently float on HF latest);
   add `uv.lock`.

## Owner decisions still open

- Personal vs. public/commercial (license); min RAM / min macOS; notarized DMG
  vs. Mac App Store; optional cloud fallback (default: off); transcript
  retention default; whether Swift owns audio in the final build (default: keep
  Python worker transitionally). Conservative reversible defaults are in place.
