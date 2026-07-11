# macOS Acceptance Checklist

None of these can be verified without a real Apple-Silicon Mac with the full
dependencies installed. Run each on the target machine and record the result
(PASS/FAIL + notes + date). **Do not mark anything PASS from a mocked test.**

## Setup / runtime

| # | Check | Result |
|---|-------|--------|
| 1 | `./setup.sh` creates the venv and installs deps cleanly (Python 3.12) | |
| 2 | First launch downloads models (Whisper, Kokoro, wake word, LLMs) | |
| 3 | Microphone permission prompt appears and, once granted, capture works | |
| 4 | Offline launch after first download (no network) still works | |
| 5 | Low-disk behavior during model download is handled gracefully | |

## Voice loop

| # | Check | Result |
|---|-------|--------|
| 6 | "Jarvis" wake word triggers listening within ~300 ms | |
| 7 | End of speech → partial transcript quickly (target < 500 ms) | |
| 8 | First spoken audio (target < 2.5 s on target hardware) | |
| 9 | **Barge-in**: speaking over Jarvis stops playback (target < 250 ms) | |
| 10 | A barged-in turn does NOT later execute its pending tool | |
| 11 | Empty/again no-speech recovers to idle within ~5 s | |

## Safety (should be verifiable behaviorally)

| # | Check | Result |
|---|-------|--------|
| 12 | `run_shell` refuses `curl`/pipes/`;` and only runs allowlisted programs | |
| 13 | Write / delete / browser mutation each require a spoken "confirm" | |
| 14 | Visiting a web page that opens `ws://127.0.0.1:8765` cannot read memory | |
| 15 | Settings cannot set filesystem allowlist to `/` or empty confirmations | |

## System integration

| # | Check | Result |
|---|-------|--------|
| 16 | Start-at-login toggle works (SMAppService) | |
| 17 | Quit stops every process (no orphan Python worker) | |
| 18 | Sleep/wake recovers audio | |
| 19 | Input/output device change (incl. Bluetooth) recovers | |
| 20 | Multi-monitor HUD placement; reduced-motion respected | |
| 21 | 8-hour idle soak: no unbounded memory growth | |

## Packaging

| # | Check | Result |
|---|-------|--------|
| 22 | Signed `.app` launches without Gatekeeper warning | |
| 23 | Notarization succeeds | |
| 24 | Update + rollback tested | |
| 25 | Uninstall removes app + offers to remove user data | |
