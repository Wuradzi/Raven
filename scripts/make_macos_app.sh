#!/bin/bash
# Build a lightweight Raven.app launcher for macOS. See docs/MACOS_SETUP.md, section 7.
#
# The app only starts scripts/run_macos.sh from THIS project folder: code, .venv, config.json
# and logs stay where they are. macOS then lists "Raven" itself under Microphone, Screen
# Recording and Accessibility instead of the terminal. Nothing is installed outside --dest,
# no sudo, no network, no secrets are read.
#
# Usage: scripts/make_macos_app.sh [--dest DIR]   (default: ~/Applications)
# Rebuild after moving the project folder. A rebuild may make macOS ask for permissions again.
set -euo pipefail

[ "$(uname -s)" = "Darwin" ] || { echo "Цей скрипт лише для macOS." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Applications"
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:?--dest потребує теки}"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Невідомий параметр: $1 (див. --help)" >&2; exit 2 ;;
  esac
done
[ -x "$ROOT/.venv/bin/python" ] || { echo "Немає .venv: спершу scripts/install_macos.sh" >&2; exit 1; }
case "$ROOT" in *\"*|*\$*|*\`*|*\\*) echo "Шлях до проєкту містить недопустимі символи." >&2; exit 1 ;; esac

APP="$DEST/Raven.app"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST"
BUILD="$TMP/Raven.app"
mkdir -p "$BUILD/Contents/MacOS" "$BUILD/Contents/Resources"

# Icon: avatar.svg -> iconset -> icns (standard macOS tools).
"$ROOT/.venv/bin/python" "$ROOT/tools/macos_app_icon.py" "$TMP/icon.png" 1024
ICONSET="$TMP/Raven.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$TMP/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) "$TMP/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/Contents/Resources/Raven.icns"

cat > "$BUILD/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Raven</string>
  <key>CFBundleDisplayName</key><string>Raven</string>
  <key>CFBundleIdentifier</key><string>local.raven.assistant</string>
  <key>CFBundleExecutable</key><string>Raven</string>
  <key>CFBundleIconFile</key><string>Raven</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Рейвен слухає голосові репліки, коли мікрофон увімкнено у вікні.</string>
  <key>NSAppleEventsUsageDescription</key><string>Рейвен надсилає системні команди (медіаклавіші, блокування екрана) лише після вашого прохання.</string>
</dict>
</plist>
PLIST

# Launcher: absolute project path baked in; stdout/stderr go to a local log, never to the system log.
cat > "$BUILD/Contents/MacOS/Raven" <<LAUNCHER
#!/bin/bash
ROOT="$ROOT"
[ -d "\$ROOT" ] || { osascript -e 'display alert "Raven" message "Теку проєкту не знайдено. Перестворіть застосунок: scripts/make_macos_app.sh"'; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "\$ROOT/logs"
if [ -n "\${RAVEN_APP_DRY_RUN:-}" ]; then echo "\$ROOT/scripts/run_macos.sh \$*"; exit 0; fi
exec /bin/bash "\$ROOT/scripts/run_macos.sh" "\$@" >>"\$ROOT/logs/macos-app.log" 2>&1
LAUNCHER
chmod 755 "$BUILD/Contents/MacOS/Raven"

plutil -lint "$BUILD/Contents/Info.plist" >/dev/null
# Ad-hoc signature: a stable identity for macOS privacy permissions on this Mac (not notarized).
codesign --force --sign - "$BUILD" >/dev/null 2>&1 || echo "Попередження: не вдалося підписати ad-hoc; дозволи можуть скидатися." >&2

rm -rf "$APP"
mv "$BUILD" "$APP"
touch "$APP"
echo "Готово: $APP"
echo "Запуск: open \"$APP\" (або з Launchpad / Spotlight). Дозволи вмикайте для пункту «Raven»."
