#!/usr/bin/env bash
# Installs Jarvis as a launchd LaunchAgent so it starts automatically on
# login and restarts if it crashes. Safe to re-run (reloads the agent).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
MAIN_PY="$PROJECT_ROOT/src/main.py"
LOG_PATH="$HOME/Library/Logs/jarvis.log"
TEMPLATE="$PROJECT_ROOT/src/system/daemon/com.krishna.jarvis.plist"
DEST="$HOME/Library/LaunchAgents/com.krishna.jarvis.plist"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "!! $PYTHON_BIN not found. Run ./setup.sh first to create .venv."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
touch "$LOG_PATH"

sed \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__MAIN_PY__|$MAIN_PY|g" \
  -e "s|__WORKING_DIR__|$PROJECT_ROOT|g" \
  -e "s|__LOG_PATH__|$LOG_PATH|g" \
  "$TEMPLATE" > "$DEST"

# Unload first (ignore failure if it wasn't loaded), then load fresh.
launchctl unload "$DEST" >/dev/null 2>&1 || true
launchctl load "$DEST"

cat <<EOF
==> Installed and loaded $DEST

Jarvis will now start automatically on login and restart if it crashes.
Logs: $LOG_PATH

Useful commands:
  launchctl list | grep com.krishna.jarvis     # check it's running
  launchctl unload $DEST                        # stop + disable autostart
  launchctl load $DEST                          # re-enable
EOF
