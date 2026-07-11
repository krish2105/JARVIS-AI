# JarvisMac — native menu-bar shell (scaffold)

This is the beginning of the native macOS app that replaces the three
LaunchAgents + terminal setup with a single normal Mac app. It is the
"no terminal" path from the audit.

## ⚠️ Status: SCAFFOLD, NOT BUILT

These Swift files were **not compiled or run** in the audit environment (no
Xcode / Swift toolchain there). They are a correct-by-construction starting
point, not a finished, verified app. Treat every claim about runtime behavior
as **NOT VERIFIED** until you build and run it in Xcode.

## What it does (by design)

- `JarvisMacApp` — `MenuBarExtra` app; owns the menu bar, launches the worker.
- `WorkerBridge` — consumes the worker's state over `ws://127.0.0.1:8765`
  (same socket as the React HUD; native client, so the server's Origin
  allowlist admits it).
- `WorkerSupervisor` — runs `python -m src.pipeline` as a **child process** of
  the app (no LaunchAgents), and `stopAll()` on Quit stops everything.
- `LoginItem` — start-at-login via `SMAppService.mainApp` (toggled in the menu,
  shown under System Settings > Login Items).
- `MenuContent` / `SettingsView` — menu + settings shell (settings tabs are
  placeholders).

## To build & verify (on your Mac)

```bash
cd apps/JarvisMac
swift build          # or: open Package.swift in Xcode 15+ (macOS 14+)
swift run            # menu-bar icon should appear
```

For a shippable `.app`, this needs to become an Xcode app target with:
- an embedded Python runtime + venv under `Contents/Resources/runtime` and the
  project under `Contents/Resources/worker` (so `WorkerSupervisor`'s paths
  resolve),
- `Info.plist` usage strings (`NSMicrophoneUsageDescription`, etc.),
- the hardened runtime + entitlements, code signing, and notarization.

See [`docs/RELEASE_PLAN.md`](../../docs/RELEASE_PLAN.md) and
[`docs/MAC_ACCEPTANCE.md`](../../docs/MAC_ACCEPTANCE.md).

## Remaining work before this replaces the LaunchAgents

1. Embedded Python runtime packaging (the hard part).
2. Full Settings/Permissions/Memory/History/Diagnostics surfaces.
3. Concurrent barge-in watcher (mic active while speaking) — the Python
   plumbing already honors cancellation; the watcher is the missing piece.
4. Signing + notarization (needs an Apple Developer identity — none available
   in the audit environment).
