#!/usr/bin/env bash
# Installs Jarvis as three launchd LaunchAgents so it starts automatically
# on login and restarts if it crashes. Safe to re-run (reloads all three).
#
# Three separate agents, not one: rumps (menu bar) and pywebview (HUD
# window) each require macOS's Cocoa main-thread run loop, and AppKit only
# tolerates one owner of it per process — and opening the microphone for
# the first time appears to hit that same main-thread constraint. So:
#   com.krishna.jarvis        runs src.main      (HUD window only)
#   com.krishna.jarvis.voice  runs src.pipeline  (microphone only)
#   com.krishna.jarvis.menubar runs src.system.menubar (menu bar only)
# They talk to each other only over the HUD WebSocket server — see
# src/main.py's module docstring for the full explanation.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
MAIN_PY="$PROJECT_ROOT/src/main.py"
LOG_PATH="$HOME/Library/Logs/jarvis.log"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "!! $PYTHON_BIN not found. Run ./setup.sh first to create .venv."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
touch "$LOG_PATH"

install_plist() {
  local template="$1" dest="$2"
  sed \
    -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
    -e "s|__MAIN_PY__|$MAIN_PY|g" \
    -e "s|__WORKING_DIR__|$PROJECT_ROOT|g" \
    -e "s|__LOG_PATH__|$LOG_PATH|g" \
    "$template" > "$dest"
  launchctl unload "$dest" >/dev/null 2>&1 || true
  launchctl load "$dest"
}

HUD_DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.plist"
VOICE_DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.voice.plist"
MENUBAR_DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.menubar.plist"

install_plist "$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.plist" "$HUD_DEST"
install_plist "$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.voice.plist" "$VOICE_DEST"
install_plist "$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.menubar.plist" "$MENUBAR_DEST"

cat <<EOF
==> Installed and loaded:
      $HUD_DEST      (HUD window)
      $VOICE_DEST      (voice pipeline / microphone)
      $MENUBAR_DEST  (menu bar icon)

Jarvis will now start automatically on login and restart if it crashes.
Logs: $LOG_PATH

Useful commands:
  launchctl list | grep com.krishna.jarvis   # check all three are running
  launchctl unload <path-to-plist>           # stop + disable that one's autostart
  launchctl load <path-to-plist>             # re-enable it
EOF
