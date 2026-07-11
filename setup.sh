#!/usr/bin/env bash
# One-time (idempotent) environment setup for Jarvis on Apple Silicon macOS.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> Checking architecture (must be arm64, not Rosetta)..."
ARCH="$(python3 -c 'import platform; print(platform.machine())' 2>/dev/null || echo unknown)"
if [ "$ARCH" != "arm64" ]; then
  echo "!! python3 reports '$ARCH', not 'arm64'."
  echo "!! You are likely running an x86 Python under Rosetta. Install/use an"
  echo "!! arm64-native Python (e.g. via 'brew install python@3.12') and re-run."
  exit 1
fi
echo "   ok: arm64"

echo "==> Checking for Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Installing..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "==> Checking Xcode Command Line Tools..."
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Xcode Command Line Tools (a system dialog will open)..."
  xcode-select --install || true
  echo "Re-run this script after the CLT install finishes."
  exit 1
fi

echo "==> Installing Homebrew dependencies (portaudio, ffmpeg)..."
brew install portaudio ffmpeg

echo "==> Setting up Python 3.12 virtual environment (.venv)..."
if command -v uv >/dev/null 2>&1; then
  uv venv .venv --python 3.12
else
  echo "   'uv' not found, falling back to venv module."
  python3.12 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies..."
if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[dev]"
else
  pip install --upgrade pip
  pip install -e ".[dev]"
fi

echo "==> Checking for Node.js (needed for the @playwright/mcp browser-control tool)..."
if ! command -v npx >/dev/null 2>&1; then
  echo "   Node.js not found. Installing via Homebrew (needed for Phase 3 browser control)..."
  brew install node
fi

echo "==> Preparing .env..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   Created .env from .env.example (no keys required by default)."
else
  echo "   .env already exists, leaving it alone."
fi

mkdir -p memories
touch memories/.gitkeep

echo "==> Verifying imports..."
python3 -c "import mlx_whisper, mlx_audio, mlx_lm, openwakeword; print('   all core imports OK')"

echo "==> Downloading openWakeWord's pretrained models (one-time, free, no account)..."
python3 -c "import openwakeword; openwakeword.utils.download_models()"

cat <<'EOF'

==> Setup complete.

Zero accounts, zero API keys needed:
  - Reasoning runs entirely on a local MLX model (see config.yaml). It
    downloads once from Hugging Face on first use (a few GB) and is fully
    offline after that.
  - The wake word uses openWakeWord's pretrained "hey jarvis" model,
    already downloaded above — no Picovoice account needed.

Next steps:
  1. source .venv/bin/activate
  2. python scripts/chat_cli.py         # Phase 1: text-only sanity check
                                         # (first run downloads the local LLM — be patient)
  3. python -m src.pipeline             # Phase 2: full voice loop
  4. python -m src.main                 # Phase 6: voice loop + menu bar + HUD

The first time Jarvis records audio, macOS will show a microphone
permission prompt — you must accept it. If it never appears, add your
terminal app manually under System Settings -> Privacy & Security -> Microphone.
EOF
