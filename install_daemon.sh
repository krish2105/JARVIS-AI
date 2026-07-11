#!/usr/bin/env bash
# Installs Jarvis as two launchd LaunchAgents so it starts automatically on
# login and restarts if it crashes. Safe to re-run (reloads both agents).
#
# Two separate agents, not one: rumps (menu bar) and pywebview (HUD window)
# each require macOS's Cocoa main-thread run loop, and AppKit only
# tolerates one owner of it per process. com.krishna.jarvis runs src.main
# (voice pipeline + HUD); com.krishna.jarvis.menubar runs src.system.menubar
# (menu bar icon only, connecting to the first one's HUD server to display
# its state) — see src/main.py and src/system/menubar.py for details.
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

MAIN_DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.plist"
MENUBAR_DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.menubar.plist"

install_plist "$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.plist" "$MAIN_DEST"
install_plist "$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.menubar.plist" "$MENUBAR_DEST"

cat <<EOF
==> Installed and loaded:
      $MAIN_DEST      (voice pipeline + HUD)
      $MENUBAR_DEST  (menu bar icon)

Jarvis will now start automatically on login and restart if it crashes.
Logs: $LOG_PATH

Useful commands:
  launchctl list | grep com.krishna.jarvis         # check both are running
  launchctl unload $MAIN_DEST      # stop voice+HUD, disable its autostart
  launchctl unload $MENUBAR_DEST # stop menu bar, disable its autostart
  launchctl load $MAIN_DEST        # re-enable voice+HUD
  launchctl load $MENUBAR_DEST   # re-enable menu bar
EOF
