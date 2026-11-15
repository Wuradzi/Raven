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
if [ "${1:-}" = "--request-permissions" ]; then
  # Asks macOS to show its own permission prompts for THIS launching app (Screen Recording,
  # Accessibility) so the app is registered in System Settings. Nothing is recorded or typed.
  exec .venv/bin/python -c "
from Quartz import CGRequestScreenCaptureAccess
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
print('request: screen_recording=', bool(CGRequestScreenCaptureAccess()))
print('request: accessibility=', bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})))"
fi
if [ "${1:-}" = "--permissions" ]; then
  # Read-only: which macOS privacy permissions THIS launching app has (no prompts, no recording).
  exec .venv/bin/python -c "from services.platform.macos_permissions import report; print('permissions:', report())"
fi
# Microphone/Accessibility/Screen Recording permissions belong to the app that hosts this
# terminal session; macOS asks for the microphone on the first recording.
exec .venv/bin/python main.py "$@"
