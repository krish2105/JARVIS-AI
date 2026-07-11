# Jarvis

A local, voice-first personal AI assistant for Apple Silicon Macs. Wake word
→ on-device speech-to-text → Claude (reasoning + tools + memory) → on-device
text-to-speech, with a lightweight always-on-top HUD and a menu bar icon.
Speech-to-text and text-to-speech run entirely on-device via MLX; the only
recurring cost is Claude API tokens for the reasoning step.

```
[Mic] → Wake word (Porcupine, on-device)
      → Speech-to-text (MLX Whisper, on-device)
      → Claude Agent SDK (reasoning + tool loop + memory)
            ├── Memory store (memories/, persists across sessions)
            └── Tools via MCP (web search, filesystem, Gmail/Drive, code exec, browser)
      → Text-to-speech (MLX/Kokoro, on-device)
      → [Speaker] + HUD (idle / listening / thinking / speaking)
```

Turn-based (wake → record → think → speak, one exchange at a time). This
was built and pushed from a Linux CI sandbox that has no microphone, no
Apple GPU, and no macOS — every module is real, working code, but the
hardware-bound phases (2, 5, 6) have only been verified with mocked
hardware in the test suite. **You need to actually run this on your Mac
to validate the acceptance tests below.**

## Requirements

- MacBook Pro, Apple Silicon (M-series), 16GB+ unified memory
- macOS Sequoia or later, arm64 native throughout (never Rosetta)
- Homebrew ([install](https://brew.sh) if you don't have it)
- Xcode Command Line Tools: `xcode-select --install`
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A free [Picovoice](https://console.picovoice.ai/) access key

## Setup

```bash
git clone <this repo>
cd jarvis
./setup.sh
```

`setup.sh` is idempotent — safe to re-run. It installs `portaudio`/`ffmpeg`
via Homebrew, creates `.venv` (Python 3.12, arm64), installs all Python
deps, checks for Node (needed for the Playwright MCP browser tool), and
copies `.env.example` to `.env` if you don't already have one.

Then:

```bash
# edit .env: set ANTHROPIC_API_KEY and PICOVOICE_ACCESS_KEY
source .venv/bin/activate
python -c "import mlx_whisper, mlx_audio, pvporcupine, claude_agent_sdk"  # should print nothing / exit 0
```

The **first time** Jarvis records audio, macOS shows a native microphone
permission dialog — accept it. Don't try to pre-grant this programmatically.
If the dialog never appears, add your terminal app manually under
**System Settings → Privacy & Security → Microphone**, then restart the
terminal.

## Build phases & how to verify each one

### Phase 0 — config skeleton
```bash
python -m src.system.config   # prints merged config.yaml + .env, secrets redacted
```

### Phase 1 — text-brain MVP (no audio yet)
```bash
python scripts/chat_cli.py
```
Have a multi-turn conversation. Ask it to search the web or read a file in
the project folder. Ask a follow-up that depends on something you said two
turns ago — session continuity is handled by resuming the Claude Agent
SDK's session id between turns (`src/brain/agent.py`).

### Phase 2 — voice loop (turn-based, fully local STT/TTS)
```bash
python -m src.pipeline
```
Say "Jarvis" (or whatever `JARVIS_WAKE_WORD` is set to) from across the
room, ask a question, and listen for a spoken reply. Watch stdout for
`[idle] → [listening] → [thinking] → [speaking]` transitions. No STT/TTS
network calls happen — only the Claude API call in the "thinking" stage.
**This is the milestone the original spec calls out as "the point where
this stops being a plan and starts being Jarvis."**

If `jarvis` isn't in Porcupine's stock keyword list on your account, either
pick a stock keyword (`computer`, `porcupine`, etc. — see
`src/audio/wake_word.py` for the full list) via `JARVIS_WAKE_WORD`, or train
a custom "Jarvis" model at console.picovoice.ai and point
`JARVIS_WAKE_WORD_MODEL_PATH` at the downloaded `.ppn` file.

### Phase 3 — real tools via MCP
Filesystem (scoped to `filesystem_allowlist` in `config.yaml`), web search,
and Bash are wired in `src/brain/tools_config.py`. Gmail/Drive and browser
control need one extra step:

- **Gmail / Google Drive**: these reuse your existing Claude account
  connectors rather than a fresh OAuth app. In your Claude account, go to
  Settings → Connectors → Gmail / Google Drive, and get that connector's
  remote MCP server URL + an OAuth token. Put them in `.env` as
  `GMAIL_MCP_URL` / `GMAIL_MCP_TOKEN` / `GDRIVE_MCP_URL` / `GDRIVE_MCP_TOKEN`.
  Leave blank to skip — Jarvis just won't register those tools.
- **Browser control**: `@playwright/mcp` runs via `npx` automatically, no
  extra setup beyond having Node installed (setup.sh checks for this).

Try: "search my Drive for the RetailPulse report and summarize it" (should
run without asking) vs. "draft an email to myself saying test" (should
speak/print the exact action and wait for you to say "confirm" — see the
confirmation gate below). Drafting always defaults to draft-only; Jarvis
never auto-sends.

### Phase 4 — memory
```bash
python scripts/chat_cli.py
# > remember that I take my coffee black, no sugar
# Ctrl-C, restart:
python scripts/chat_cli.py
# > how do I take my coffee
```
Facts persist in `memories/` (see `src/brain/memory.py`, which implements
Anthropic's memory tool file operations — `view`/`create`/`str_replace`/
`insert`/`delete`/`rename` — against that directory, with path-traversal
protection). `memories/preferences.md` is seeded on first run if missing.

### Phase 5 — menu bar + background daemon
```bash
./install_daemon.sh
```
Reboot (or log out/in). The menu bar icon should appear automatically with
no manual terminal command, and the wake word should work immediately.
Toggle "Pause Jarvis" from the menu to stop it listening without quitting.

### Phase 6 — HUD
```bash
python -m src.main
```
A small borderless, always-on-top, transparent HUD appears in the screen
corner set by `hud.corner` in `config.yaml`, and updates in real time as
you talk to Jarvis. It's click-through while idle so it never steals focus.

> **Known rough edge**: `src/main.py` runs the HUD (pywebview/Cocoa) on the
> main thread and the menu bar (rumps/Cocoa) in a background thread. Both
> want to own macOS's main run loop, which is a known fragile combination.
> If the menu bar icon misbehaves when launched this way, run
> `python -m src.system.menubar` as its own process instead and drop the
> combined `src/main.py` entrypoint from `install_daemon.sh`.

## Running the full test suite

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite runs anywhere (it was written and verified in a Linux CI
sandbox with no MLX/Porcupine/macOS available) by exercising the
hardware-independent logic directly — the memory tool's file operations,
the confirmation-gate/filesystem-scoping logic, config parsing, and the
audio modules' pure logic (sentence splitting, wav writing, keyword
resolution) against mocked `mlx_whisper`/`mlx_audio`/`pvporcupine`/
`sounddevice`. It does **not** and cannot verify actual transcription
accuracy, TTS audio quality, wake-word detection from real audio, or
launchd/rumps/pywebview behavior — those are the phase acceptance tests
above, and they require your actual Mac.

## Safety rules (enforced in code, not just documented)

- **Confirmation gate**: any tool call matching `require_confirmation_for`
  in `config.yaml` (`send_email`, `delete_file`, `run_shell_command`,
  `Write`, `Edit`) is intercepted by a `PreToolUse` hook
  (`src/brain/tools_config.py`) that speaks/prints the exact pending action
  and denies it unless you explicitly say/type "confirm". This runs via
  Claude Agent SDK hooks rather than `allowed_tools`/`can_use_tool`,
  because hooks are the only mechanism that fires for *every* tool call
  regardless of permission mode.
- **Filesystem scoping**: `Read`/`Write`/`Edit` are denied outside the
  directories listed in `filesystem_allowlist` (default: `~/Documents`,
  `~/Desktop`, the project folder) — never widen this to the whole home
  directory or root.
- **Audit log**: every tool call's name, arguments, and result is logged to
  `~/Library/Logs/jarvis.log` via a `PostToolUse` hook.
- **Mic discipline**: the mic is only actively recording between wake-word
  detection and end-of-turn silence (`src/audio/recorder.py`); wake-word
  listening (`src/audio/wake_word.py`) inspects small rolling frames and
  discards them immediately — nothing is ever continuously streamed
  anywhere, local or cloud.
- **Secrets**: API keys live only in `.env` (gitignored), never hardcoded,
  never committed. `python -m src.system.config` redacts them when printed.

## Troubleshooting

- **No mic permission dialog ever appeared**: add your terminal app
  manually under System Settings → Privacy & Security → Microphone, then
  restart the terminal.
- **`pvporcupine` fails to import**: confirm arm64 Python —
  `python3 -c "import platform; print(platform.machine())"` should print
  `arm64`, not `x86_64`. If it prints `x86_64`, you're running under
  Rosetta; install an arm64-native Python (e.g. `brew install python@3.12`)
  and re-run `setup.sh`.
- **MLX models "work" but are slow**: check nothing is silently running on
  CPU — `mlx-whisper`/`mlx-audio` should use the GPU via MLX automatically
  on Apple Silicon. If not, delete `.venv` and re-run `setup.sh` in a clean
  arm64-only environment.
- **launchd daemon doesn't start on login**: check
  `~/Library/Logs/jarvis.log`, and confirm the installed plist's
  `ProgramArguments` points at `.venv/bin/python`, not the system Python
  (`install_daemon.sh` does this substitution automatically — re-run it if
  you moved the project folder).
- **Adding a push-to-talk hotkey later**: if you add one via `pynput`,
  macOS requires granting Accessibility + Input Monitoring permission to
  the terminal/app separately from microphone access.

## Project structure

```
jarvis/
├── setup.sh                  install_daemon.sh
├── config.yaml                .env.example / .env (gitignored)
├── memories/                  persisted memory-tool files
├── src/
│   ├── main.py                 entrypoint: pipeline + menu bar + HUD
│   ├── pipeline.py             wake → stt → brain → tts → HUD state glue
│   ├── audio/                  wake_word.py, recorder.py, stt.py, tts.py
│   ├── brain/                  agent.py, memory.py, tools_config.py
│   ├── hud/                    server.py (WebSocket) + web/ (frontend)
│   └── system/                 config.py, menubar.py, daemon/*.plist
├── scripts/chat_cli.py        text-only harness (Phase 1)
└── tests/                      pytest suite (hardware-independent)
```

## Cost

Recurring cost is Claude API token usage only — STT and TTS are fully
on-device via MLX, and everything else (Porcupine, memory tool, launchd,
rumps, pywebview) is free for personal use.

## Stretch goals (not built — see original spec)

Full-duplex/barge-in via Pipecat, Home Assistant integration, proactive
calendar-aware speech, computer-use tool access, and a cloned custom voice
are deliberately out of scope for v1.
