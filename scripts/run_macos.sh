#!/bin/bash
# Start Raven on macOS from the project .venv. All arguments go to main.py
# (e.g. scripts/run_macos.sh --doctor | --text-only | --setup). Prints no secrets.
set -euo pipefail

[ "$(uname -s)" = "Darwin" ] || { echo "Цей скрипт лише для macOS." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || { echo "Немає .venv: спершу scripts/install_macos.sh" >&2; exit 1; }
[ -f config.json ] || { echo "Немає config.json: спершу scripts/install_macos.sh" >&2; exit 1; }

export PYTHONUTF8=1
# Microphone/Accessibility/Screen Recording permissions belong to the app that hosts this
# terminal session; macOS asks for the microphone on the first recording.
exec .venv/bin/python main.py "$@"
