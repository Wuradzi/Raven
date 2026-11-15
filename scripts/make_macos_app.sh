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
  <key>NSDocumentsFolderUsageDescription</key><string>Тека проєкту Рейвена (код, налаштування, журнали) лежить у Документах.</string>
  <key>NSDesktopFolderUsageDescription</key><string>Рейвен шукає файли на робочому столі лише на ваше прохання.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>Рейвен шукає файли в Завантаженнях лише на ваше прохання.</string>
  <key>NSAppleEventsUsageDescription</key><string>Рейвен надсилає системні команди (медіаклавіші, блокування екрана) лише після вашого прохання.</string>
</dict>
</plist>
PLIST

# Launcher: absolute project path baked in; stdout/stderr go to a local log, never to the system log.
# Shell part of the launcher. It lives in Resources: macOS checks privacy permissions against the
# main executable, and a script main executable would be judged as /bin/bash, not as Raven.
cat > "$BUILD/Contents/Resources/launch.sh" <<LAUNCHER
#!/bin/bash
ROOT="$ROOT"
[ -d "\$ROOT" ] || { osascript -e 'display alert "Raven" message "Теку проєкту не знайдено. Перестворіть застосунок: scripts/make_macos_app.sh"'; exit 1; }
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "\$ROOT/logs"
if [ -n "\${RAVEN_APP_DRY_RUN:-}" ]; then echo "\$ROOT/scripts/run_macos.sh \$*"; exit 0; fi
if [ -n "\${RAVEN_APP_CHECK:-}" ]; then
  # Безпечна перевірка доступу до теки проєкту: лише читає файл, Raven не запускає.
  if /bin/cat "\$ROOT/scripts/run_macos.sh" >/dev/null 2>&1; then echo "check: доступ до теки проєкту є"; else echo "check: macOS не дає доступу до теки проєкту"; fi >>"\$ROOT/logs/macos-app.log" 2>&1
  exit 0
fi
exec /bin/bash "\$ROOT/scripts/run_macos.sh" "\$@" >>"\$ROOT/logs/macos-app.log" 2>&1
LAUNCHER
chmod 755 "$BUILD/Contents/Resources/launch.sh"

# Main executable: a tiny compiled program that runs launch.sh as its child and waits, so the
# child (bash, then Python) stays attributed to Raven for Documents, microphone and other permissions.
cat > "$TMP/launcher.c" <<'CSRC'
#include <limits.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>

extern char **environ;

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) return 1;
    snprintf(script, sizeof script, "%s/../Resources/launch.sh", dirname(real));
    char **args = calloc((size_t)argc + 2, sizeof *args);
    if (!args) return 1;
    args[0] = "/bin/bash";
    args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    pid_t child;
    int status = 0;
    if (posix_spawn(&child, "/bin/bash", NULL, NULL, args, environ) != 0) return 1;
    while (waitpid(child, &status, 0) < 0) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
CSRC
cc -O2 -Wall -o "$BUILD/Contents/MacOS/Raven" "$TMP/launcher.c"

plutil -lint "$BUILD/Contents/Info.plist" >/dev/null
# Ad-hoc signature: a stable identity for macOS privacy permissions on this Mac (not notarized).
codesign --force --sign - "$BUILD" >/dev/null 2>&1 || echo "Попередження: не вдалося підписати ad-hoc; дозволи можуть скидатися." >&2

rm -rf "$APP"
mv "$BUILD" "$APP"
touch "$APP"
echo "Готово: $APP"
echo "Запуск: open \"$APP\" (або з Launchpad / Spotlight). Дозволи вмикайте для пункту «Raven»."
