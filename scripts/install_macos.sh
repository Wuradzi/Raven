#!/bin/bash
# Raven installer for macOS (Apple Silicon and Intel). See docs/MACOS_SETUP.md.
#
# Safe by design: it never prints or stores API keys, never overwrites an existing
# config.json or .env, never uses sudo, and installs Homebrew packages only with
# --install-brew-deps. NOT yet validated on a real Mac (manual smoke test required).
#
# Usage: scripts/install_macos.sh [--install-brew-deps] [--with-whisper] [--with-native-ui]
#                                 [--with-vision] [--check-only]
# Python override: RAVEN_PYTHON=/path/to/python3.12 scripts/install_macos.sh
set -euo pipefail

BREW_FORMULAS="python@3.12 portaudio"
INSTALL_BREW=0
WITH_WHISPER=0
WITH_NATIVE_UI=0
WITH_VISION=0
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --install-brew-deps) INSTALL_BREW=1 ;;
    --with-whisper) WITH_WHISPER=1 ;;
    --with-native-ui) WITH_NATIVE_UI=1 ;;
    --with-vision) WITH_VISION=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Невідомий параметр: $arg (див. --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'ПОМИЛКА: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "Цей скрипт лише для macOS (uname = $(uname -s))."
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
say "Raven: установка на macOS $(sw_vers -productVersion 2>/dev/null || echo '?') ($(uname -m)), тека $ROOT"

# --- 1. Homebrew and system libraries (explicit opt-in install) --------------
if command -v brew >/dev/null 2>&1; then
  say "Homebrew: знайдено."
  missing=""
  for formula in $BREW_FORMULAS; do
    brew list --versions "$formula" >/dev/null 2>&1 || missing="$missing $formula"
  done
  if [ -n "$missing" ]; then
    if [ "$INSTALL_BREW" = 1 ] && [ "$CHECK_ONLY" = 0 ]; then
      say "Встановлюю через Homebrew:$missing"
      # shellcheck disable=SC2086
      brew install $missing
    else
      say "Бракує пакетів Homebrew:$missing"
      say "  Встановіть: brew install$missing   (або запустіть із --install-brew-deps)"
    fi
  fi
else
  say "Homebrew не знайдено. Для PortAudio і Python 3.12 встановіть його з https://brew.sh,"
  say "потім: brew install python@3.12 portaudio"
fi

# --- 2. Python >= 3.10 (3.12 recommended) ------------------------------------
find_python() {
  local candidate
  for candidate in "${RAVEN_PYTHON:-}" python3.12 python3.13 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}
PYTHON="$(find_python)" || fail "Потрібен Python 3.10+ (рекомендовано 3.12): brew install python@3.12"
say "Python: $("$PYTHON" --version 2>&1) ($PYTHON)"
case "$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" in
  3.12) ;;
  *) say "Увага: еталонна версія — 3.12; інші версії менш перевірені." ;;
esac

[ "$CHECK_ONLY" = 0 ] || { say "--check-only: далі лише перевірка середовища."; }

# --- 3. Virtual environment and dependencies --------------------------------
if [ "$CHECK_ONLY" = 0 ]; then
  if [ ! -x .venv/bin/python ]; then
    say "Створюю .venv"
    "$PYTHON" -m venv .venv
  else
    say ".venv уже є — використовую його."
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements-macos.txt
  [ "$WITH_WHISPER" = 0 ] || .venv/bin/python -m pip install -r requirements-whisper.txt
  [ "$WITH_NATIVE_UI" = 0 ] || .venv/bin/python -m pip install -r requirements-native.txt
  [ "$WITH_VISION" = 0 ] || .venv/bin/python -m pip install -r requirements-optional.txt
fi

# --- 4. Local config and .env (never overwritten) ---------------------------
if [ "$CHECK_ONLY" = 0 ]; then
  if [ -f config.json ]; then
    say "config.json уже є — не змінюю."
  else
    cp config.example.json config.json
    say "Створено config.json із config.example.json."
  fi
  if [ -f .env ]; then
    say ".env уже є — не змінюю."
  elif [ -f .env.example ]; then
    cp .env.example .env
    chmod 600 .env
    say "Створено порожній .env із шаблону. Додайте ключі самі (GROQ_API_KEY, GEMINI_API_KEY),"
    say "відкривши файл у редакторі; скрипт ключів не запитує й не зберігає."
  fi
  mkdir -p data cache logs models
fi

# --- 5. Environment and permissions check (read-only) -----------------------
if [ -x .venv/bin/python ]; then
  .venv/bin/python tools/macos_check.py || say "Перевірка середовища виявила зауваження (див. вище)."
else
  "$PYTHON" tools/macos_check.py || true
fi

cat <<'TEXT'

Далі (див. docs/MACOS_SETUP.md):
  1. Українська голосова синтез: Системні налаштування → Універсальний доступ → Промовляти вміст
     → Системний голос → Керувати голосами… → Ukrainian (Lesya). Перевірка: say -v '?' | grep uk_UA
  2. Дозволи для програми, з якої запускаєте Raven (Terminal/iTerm/IDE):
     Мікрофон; Універсальний доступ (блокування екрана, медіаклавіші); Запис екрана (знімок екрана).
  3. Ключі API — лише у .env, не в командному рядку.
  4. Запуск: scripts/run_macos.sh --doctor, потім scripts/run_macos.sh --text-only або scripts/run_macos.sh
TEXT
