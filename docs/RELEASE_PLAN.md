# Release Plan — no-terminal macOS app

Goal: ship Jarvis as a normal `.app` the user double-clicks — no terminal, no
Homebrew, no system Python, no Node. Status: **not started beyond the Swift
scaffold**; the steps below are the plan, not completed work.

## 1. Embed the runtime (the hard part)

The app must carry its own Python + dependencies so `WorkerSupervisor` can run
`python -m src.pipeline` from inside the bundle.

- Use `python-build-standalone` (or a relocatable framework build) placed at
  `JarvisMac.app/Contents/Resources/runtime`.
- Create the venv against that interpreter and install the project + deps
  (mlx, mlx-whisper, mlx-audio, openwakeword, sounddevice, webrtcvad, …).
- Copy the project into `Contents/Resources/worker`.
- Verify every native wheel (sounddevice/PortAudio, webrtcvad) loads from the
  relocated path.
- @playwright/mcp needs Node — either bundle a Node runtime or make browser
  control an optional download; do NOT require the user to install Node.

## 2. App target + Info.plist

- Convert the SwiftPM scaffold into an Xcode **app** target (MenuBarExtra +
  `LSUIElement`/agent app so there's no Dock icon if desired).
- Usage strings: `NSMicrophoneUsageDescription`, and only add
  Accessibility/Automation/Screen-Recording prompts when those features ship.

## 3. Hardened runtime + entitlements

- Enable Hardened Runtime.
- Entitlements: `com.apple.security.device.audio-input`. Add
  `disable-library-validation` only if the embedded Python loads unsigned
  dylibs and you cannot re-sign them.
- Sign the embedded Python, all `.dylib`/`.so`, and the app (deep sign).

## 4. Sign + notarize

```bash
# Requires a "Developer ID Application" identity — NOT available in the audit env.
codesign --deep --force --options runtime \
  --sign "Developer ID Application: <NAME> (<TEAMID>)" JarvisMac.app
ditto -c -k --keepParent JarvisMac.app JarvisMac.zip
xcrun notarytool submit JarvisMac.zip --keychain-profile "<PROFILE>" --wait
xcrun stapler staple JarvisMac.app
```

**Signing/notarization is BLOCKED in the audit environment** (no Apple Developer
identity). Build the unsigned dev artifact and run the above on a machine that
has the identity.

## 5. Distribution

- Package as a signed DMG (drag-to-Applications) or PKG.
- Update mechanism: Sparkle (signed appcast) or a simple signed-DMG check.
- Uninstall flow: remove the app, offer to delete `~/Library/Logs/jarvis*`,
  `memories/`, and any Application Support data.
- Ship SBOM + THIRD_PARTY_NOTICES + release notes; document rollback.

## 6. Login item

Already scaffolded via `SMAppService.mainApp` (`LoginItem.swift`) — no
LaunchAgent plists in the shipped product. Remove `install_daemon.sh` from the
user-facing flow once the app supervises the worker.
