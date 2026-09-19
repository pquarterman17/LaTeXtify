#!/usr/bin/env bash
# ============================================================
#  LaTeXtify launcher (macOS and Linux)
#  Run:  ./startup.sh      (or: bash startup.sh)
#  On failure it writes latextify-startup.log NEXT TO this
#  script and prints it, so the error is easy to copy-paste.
#  (macOS: to double-click it, rename to startup.command.)
# ============================================================
set -u

cd "$(dirname "$0")" || exit 1
LOG="$(pwd)/latextify-startup.log"
VENVPY=".venv/bin/python"

show_log() {
  echo
  echo "============================================================"
  echo " LaTeXtify could not start."
  echo " The full error log was saved next to this script:"
  echo
  echo "   $LOG"
  echo
  echo " Copy everything between the dashed lines below and share it:"
  echo " ------------------------------------------------------------"
  cat "$LOG"
  echo " ------------------------------------------------------------"
  echo "============================================================"
}

{
  echo "LaTeXtify startup log"
  echo "Run at $(date)"
  echo "============================================================"
} > "$LOG"

# Fast path: if the environment already works, skip dependency setup.
needs_setup=1
if [ -x "$VENVPY" ] && "$VENVPY" -c "import latextify.gui.server" >/dev/null 2>&1; then
  needs_setup=0
fi

if [ "$needs_setup" -eq 1 ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed -- installing it now..."
    echo "--- installing uv via official installer ---" >> "$LOG"
    curl -LsSf https://astral.sh/uv/install.sh 2>> "$LOG" | sh >> "$LOG" 2>&1 || true
    [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      {
        echo "ERROR: automatic uv install failed. Install it manually from"
        echo "https://docs.astral.sh/uv/ then run this again."
      } >> "$LOG"
      show_log
      exit 1
    fi
  fi
  echo "Setting up LaTeXtify - first run installs dependencies, please wait..."
  echo "--- uv sync --extra gui ---" >> "$LOG"
  uv sync --extra gui >> "$LOG" 2>&1 || true
fi

if [ ! -x "$VENVPY" ]; then
  show_log
  exit 1
fi

echo "Starting LaTeXtify. Your browser should open at http://127.0.0.1:8501"
echo "Keep this terminal open while you use it. Press Ctrl+C to stop."
echo "--- launch: python -m latextify gui ---" >> "$LOG"

"$VENVPY" -m latextify gui "$@" 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}

# 0 = clean exit; 130 = Ctrl+C (graceful stop). Anything else is a failure.
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
  show_log
  exit 1
fi

echo "LaTeXtify has stopped."
