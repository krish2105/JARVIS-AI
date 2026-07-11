#!/usr/bin/env bash
# Build Jarvis.app — a minimal .app bundle whose MAIN process is the voice
# pipeline itself (via exec). Launched through LaunchServices (`open`), macOS
# attributes microphone access to "Jarvis" — a stable app identity the user
# grants once — instead of to a bare launchd python, which macOS silently
# denies. This is what makes hands-free "Hey Jarvis" work without a full
# Xcode/SwiftUI build or an Apple Developer signing identity.
#
#   Usage:  bash scripts/build_macos_app.sh [output-path]
#   Default output: ~/Applications/Jarvis.app
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$HOME/Applications/Jarvis.app}"
PY="$PROJECT_ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "!! venv python not found at $PY — set up the project venv first." >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Jarvis</string>
    <key>CFBundleDisplayName</key><string>Jarvis</string>
    <key>CFBundleIdentifier</key><string>com.krishna.jarvis.voiceapp</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>jarvis</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Jarvis listens for the "Hey Jarvis" wake word and your spoken commands.</string>
</dict>
</plist>
PLIST

# The launcher execs python so python BECOMES the app's main process (same PID),
# keeping the bundle's TCC identity. PROJECT_ROOT and PY are baked in at build time.
cat > "$APP/Contents/MacOS/jarvis" <<LAUNCH
#!/bin/bash
cd "$PROJECT_ROOT" || exit 1
exec "$PY" -m src.pipeline >> "\$HOME/Library/Logs/jarvis.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/jarvis"

# Ad-hoc code-sign so the mic grant persists across launches (TCC keys on the
# signing identity). No Apple Developer ID needed for local use.
codesign --force --deep --sign - "$APP" 2>/dev/null && echo "ad-hoc signed" || echo "(codesign skipped)"

echo "Built: $APP"
echo "Launch:  open \"$APP\""
