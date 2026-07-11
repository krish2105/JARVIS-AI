# Jarvis

A local, voice-first personal AI assistant for Apple Silicon Macs. Wake word
→ on-device speech-to-text → local reasoning → on-device text-to-speech,
with a lightweight always-on-top HUD and a menu bar icon.

**This is the fully-local, zero-signup edition: no Anthropic API key, no
Picovoice account, no cloud reasoning call, no per-token cost, ever.**
Speech-to-text, text-to-speech, wake word, *and* the reasoning step all run
on-device. The only paid dependency the original design called for — the
Claude API — has been swapped for a local instruct model via `mlx-lm` with
a hand-built tool-calling loop, and the wake word uses openWakeWord (a
pretrained "hey jarvis" model, no account) instead of Picovoice Porcupine
(which requires signing up). See [Architecture note](#architecture-note-why-no-claude)
for the reasoning trade-offs.

```
[Mic] → Wake word (openWakeWord, on-device, pretrained "hey jarvis" model)
      → Speech-to-text (MLX Whisper, on-device)
      → Local LLM + tool loop (mlx-lm, on-device — no cloud call)
            ├── Memory store (memories/, persists across sessions)
            └── Local tools (memory, files, web search, shell, browser control)
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
- That's it — no Anthropic account, no Picovoice account, no billing,
  no signups of any kind. The wake word uses openWakeWord's pretrained
  "hey jarvis" model, downloaded for free with no account needed.

## Setup

```bash
git clone <this repo>
cd jarvis
./setup.sh
```

`setup.sh` is idempotent — safe to re-run. It installs `portaudio`/`ffmpeg`
via Homebrew, creates `.venv` (Python 3.12, arm64), installs all Python
deps (including `mlx-lm` for local reasoning), downloads openWakeWord's
pretrained models (free, no account), checks for Node (needed for the
Playwright MCP browser tool), and copies `.env.example` to `.env` if you
don't already have one — no keys need to be filled in.

Then:

```bash
source .venv/bin/activate
python -c "import mlx_whisper, mlx_audio, mlx_lm, openwakeword"  # should exit 0
```

The **first time** Jarvis records audio, macOS shows a native microphone
permission dialog — accept it. Don't try to pre-grant this programmatically.
If the dialog never appears, add your terminal app manually under
**System Settings → Privacy & Security → Microphone**, then restart the
terminal.

The **first time** Jarvis actually thinks (any Phase 1+ test below), it
downloads the local model from Hugging Face — a few GB, one-time, needs a
network connection. After that it's cached under `~/.cache/huggingface`
and every subsequent run is fully offline.

## Build phases & how to verify each one

### Phase 0 — config skeleton
```bash
python -m src.system.config   # prints merged config.yaml + .env, secrets redacted
```

### Phase 1 — text-brain MVP (no audio yet)
```bash
python scripts/chat_cli.py
```
Have a multi-turn conversation. Ask it to search the web, read a file in the
project folder, or run a shell command. Ask a follow-up that depends on
something you said two turns ago — conversation history is kept in memory
for the lifetime of the process (`src/brain/agent.py`'s `JarvisSession`).

### Phase 2 — voice loop (turn-based, fully local STT/TTS/reasoning)
```bash
python -m src.pipeline
```
Say "Jarvis" (or whatever `JARVIS_WAKE_WORD` is set to) from across the
room, ask a question, and listen for a spoken reply. Watch stdout for
`[idle] → [listening] → [thinking] → [speaking]` transitions. **Nothing
here makes a network call after the one-time model download** — this is
the milestone the original spec calls out as "the point where this stops
being a plan and starts being Jarvis," now with zero recurring cost.

openWakeWord ships six pretrained models (`hey jarvis`, `alexa`, `hey
mycroft`, `hey rhasspy`, `current weather`, `timers`); `JARVIS_WAKE_WORD`
just needs to be a substring that matches one of their names (default
`jarvis` matches the bundled `hey_jarvis` model). If you ever train a
fully custom model, point `JARVIS_WAKE_WORD_MODEL_PATH` at it instead.

### Phase 3 — tools
`src/brain/tools.py` registers: `memory`, `read_file`/`write_file` (scoped
to `filesystem_allowlist` in `config.yaml`), `run_shell`, and `web_search`
(free — scrapes DuckDuckGo's HTML endpoint, no API key). Browser control
(`@playwright/mcp`) is discovered dynamically over a minimal MCP stdio
client (`src/brain/mcp_client.py`) if Node is installed; it degrades
silently to "unavailable" otherwise.

Try: "search the web for the weather in Boston" (runs without asking) vs.
"write a file called test.txt in Documents saying hello" (should speak/print
the exact action and wait for you to say "confirm" — see the confirmation
gate below).

> **Known gap vs. the original spec**: Gmail/Google Drive integration is
> not implemented in this local-LLM edition. The original design reused
> Claude Agent SDK's native MCP client to wire in your account's Gmail/Drive
> connectors; without that SDK, wiring the same connectors would mean
> building a full HTTP+SSE JSON-RPC MCP client with OAuth handling from
> scratch, which wasn't done here. If you want it, the cleanest path is
> adding a proper MCP HTTP client alongside `mcp_client.py`'s stdio one.

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
protection; the command set is Anthropic's design, but it's just plain
Python now, no Claude dependency). `memories/preferences.md` is seeded on
first run if missing.

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
sandbox with no MLX/macOS available) by exercising the hardware-independent
logic directly — the memory tool's file operations, the confirmation-gate/
filesystem-scoping logic, the tool-calling loop's JSON parsing (with a fake
local LLM standing in for `mlx-lm`), config parsing, and the audio modules'
pure logic (sentence splitting, wav writing, wake-word score matching)
against mocked `mlx_whisper`/`mlx_audio`/`openwakeword`/`sounddevice`. It
does **not** and cannot verify actual local
model quality/tool-calling reliability, transcription accuracy, TTS audio
quality, wake-word detection from real audio, or launchd/rumps/pywebview
behavior — those are the phase acceptance tests above, and they require
your actual Mac.

## Architecture note: why no Claude?

The original design for this project used Claude (via the Claude Agent
SDK) as the reasoning brain, with STT/TTS local and only the reasoning step
hitting the API — a very cheap setup, but not literally $0. This edition
was built after an explicit request for zero ongoing cost, which means the
brain had to move on-device too. The trade-offs that come with that:

- **Tool-calling reliability is weaker, and this is a real risk, not a
  quality nitpick.** Claude models are trained specifically for reliable
  structured tool use; local models are instructed to emit a JSON
  tool-call object via prompt engineering (`src/brain/agent.py`), which is
  inherently less robust. In testing, a 3B model didn't just fail to call
  a tool — it **fabricated an entire fake tool execution**: it invented a
  shell script, invented a fake confirmation exchange, and then claimed
  "the daemon has been installed and is now running," all without ever
  emitting a real tool call or touching the filesystem. `model.local`
  defaults to 8B for this reason, with a hardened system prompt that
  explicitly forbids claiming an action succeeded without a real tool
  result. This significantly reduces but does not eliminate the risk —
  **always check `~/Library/Logs/jarvis.log`** (see Safety rules below) if
  Jarvis claims to have done something consequential; only lines starting
  `TOOL_CALL` reflect something that actually happened. If you see this
  behavior at 8B, bump to `local_heavy` (14B) or larger.
- **Reasoning quality is weaker**, especially for multi-step plans, math,
  and anything requiring broad world knowledge. Straightforward Q&A,
  reminders, and simple tool calls work fine.
- **Gmail/Drive integration was dropped** (see the Phase 3 note above) —
  it depended on the Claude Agent SDK's built-in MCP client for your
  account's connectors.
- **Web search is a free DuckDuckGo HTML scrape**, not an official API —
  it can break if DuckDuckGo changes their markup, in which case swap in
  any free-tier search API in `src/brain/web_search.py`.

If you'd rather have Claude's reasoning quality and are fine with the
(small, usage-based) API cost, the swap back is mostly confined to
`src/brain/agent.py`, `src/brain/tools.py`, and `config.yaml`'s `model:`
section — the audio pipeline, HUD, memory tool, and safety gates are
unchanged either way.

## Safety rules (enforced in code, not just documented)

- **Confirmation gate**: any tool call matching `require_confirmation_for`
  in `config.yaml` (`delete_file`, `run_shell_command`, `Write`) is
  intercepted by `ToolGuard.check()` (`src/brain/tools.py`), called by the
  tool loop before every execution, which speaks/prints the exact pending
  action and denies it unless you explicitly say/type "confirm".
- **Filesystem scoping**: `read_file`/`write_file` are denied outside the
  directories listed in `filesystem_allowlist` (default: `~/Documents`,
  `~/Desktop`, the project folder) — never widen this to the whole home
  directory or root. This check is baked directly into the tool
  implementations, so the model has no other path to the filesystem.
- **Audit log**: every tool call's name, arguments, and result is logged to
  `~/Library/Logs/jarvis.log` via `ToolGuard.log()` as a `TOOL_CALL` line —
  this is the ground truth for what Jarvis actually did. If Jarvis says it
  did something and there's no matching `TOOL_CALL` line, it didn't happen
  (see the architecture note above on local models fabricating actions).
- **Mic discipline**: the mic is only actively recording between wake-word
  detection and end-of-turn silence (`src/audio/recorder.py`); wake-word
  listening (`src/audio/wake_word.py`) inspects small rolling frames and
  discards them immediately — nothing is ever continuously streamed
  anywhere, local or cloud.
- **Secrets**: there are none by default — no API keys required anywhere
  in this edition. If you set optional overrides in `.env`, it's gitignored
  and never committed.

## Troubleshooting

- **No mic permission dialog ever appeared**: add your terminal app
  manually under System Settings → Privacy & Security → Microphone, then
  restart the terminal.
- **`openwakeword` fails to import, or the model download hangs**: confirm
  arm64 Python — `python3 -c "import platform; print(platform.machine())"`
  should print `arm64`, not `x86_64`. If it prints `x86_64`, you're running
  under Rosetta; install an arm64-native Python (e.g.
  `brew install python@3.12`) and re-run `setup.sh`. The model download
  itself needs a network connection the first time only.
- **Wake word never triggers, or triggers on the wrong word**: try lowering
  `audio.wake_word_sensitivity` in `config.yaml` (e.g. to `0.4`) if it's not
  triggering, or raising it (e.g. to `0.8`) if it's too trigger-happy.
- **First response is very slow / model download seems stuck**: the first
  call downloads the model from Hugging Face (a few GB) — check your
  network connection and be patient. Subsequent runs are fast and fully
  offline.
- **MLX models "work" but are slow**: check nothing is silently running on
  CPU — `mlx-whisper`/`mlx-audio`/`mlx-lm` should use the GPU via MLX
  automatically on Apple Silicon. If not, delete `.venv` and re-run
  `setup.sh` in a clean arm64-only environment.
- **Jarvis claims it did something (ran a command, installed something,
  wrote a file) but you're not sure it's real**: check
  `~/Library/Logs/jarvis.log` for a matching `TOOL_CALL` line. If there
  isn't one, it didn't happen — the model narrated it in plain text
  instead of actually calling the tool. This is a known failure mode of
  small local models (see the architecture note above); switch
  `model.local` to `model.local_heavy`'s value or something bigger if you
  see it, and never take a consequential claim at face value without
  checking the log.
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
│   ├── brain/
│   │   ├── agent.py              local tool-calling loop (JarvisSession, run_turn)
│   │   ├── local_llm.py          mlx-lm model loading + chat()
│   │   ├── tools.py              tool registry + ToolGuard (confirm/scope/log)
│   │   ├── tool_types.py         Tool dataclass
│   │   ├── memory.py             memory-tool file ops
│   │   ├── web_search.py         free DuckDuckGo search
│   │   ├── mcp_client.py         minimal stdio MCP client
│   │   └── browser_tools.py      wraps @playwright/mcp as local tools
│   ├── hud/                    server.py (WebSocket) + web/ (frontend)
│   └── system/                 config.py, menubar.py, daemon/*.plist
├── scripts/chat_cli.py        text-only harness (Phase 1)
└── tests/                      pytest suite (hardware-independent)
```

## Cost

$0 recurring, $0 up front. STT, TTS, and reasoning are all on-device via
MLX; openWakeWord, the memory tool, launchd, rumps, and pywebview are all
free and open-source with no account needed. The only network usage is the
one-time model downloads (LLM + wake word) on first run.

## Stretch goals (not built — see original spec)

Full-duplex/barge-in via Pipecat, Home Assistant integration, proactive
calendar-aware speech, computer-use tool access, a cloned custom voice, and
Gmail/Drive integration (see the architecture note above) are deliberately
out of scope for this edition.
