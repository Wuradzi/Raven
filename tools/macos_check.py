"""Read-only macOS environment check for Raven (no prompts, no recording, no secrets).

Run: python tools/macos_check.py   (installer runs it automatically)
Exit code 1 only when something REQUIRED is missing; permission gaps are warnings because
macOS grants them per host application (Terminal, iTerm, IDE) at first use.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REQUIRED_TOOLS = ('open', 'say', 'afplay', 'osascript', 'screencapture')
PERMISSION_HINTS = {
    'microphone': 'Системні налаштування → Конфіденційність і безпека → Мікрофон (голосовий режим)',
    'accessibility': 'Системні налаштування → Конфіденційність і безпека → Універсальний доступ '
                     '(блокування екрана, медіаклавіші)',
    'screen_recording': 'Системні налаштування → Конфіденційність і безпека → Запис екрана (знімок екрана)',
}


def _tools(which):
    return [('OK' if which(name) else 'FAIL', f'утиліта {name}', which(name) or 'не знайдено')
            for name in REQUIRED_TOOLS]


def _ukrainian_voice(run):
    try:
        output = run(['say', '-v', '?'], capture_output=True, timeout=8, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ('WARN', 'голос say', 'не вдалося отримати список голосів')
    lines = [line for line in output.decode('utf-8', 'replace').splitlines() if ' uk_' in line]
    if lines:
        return ('OK', 'український голос', lines[0].split('#')[0].strip())
    return ('WARN', 'український голос', 'немає uk_UA: Системні налаштування → Універсальний доступ → '
            'Промовляти вміст → Керувати голосами → Ukrainian (Lesya)')


def _audio_input():
    try:
        import sounddevice
        devices = [d for d in sounddevice.query_devices() if d.get('max_input_channels', 0) > 0]
    except Exception as exc:  # PortAudio missing or CoreAudio refused: report, do not crash.
        return ('FAIL', 'PortAudio / sounddevice', f'{type(exc).__name__}: brew install portaudio')
    if not devices:
        return ('WARN', 'мікрофон', 'пристроїв введення не знайдено (запис не виконувався)')
    return ('OK', 'мікрофон', f'пристроїв введення: {len(devices)} (запис не виконувався)')


def _permissions():
    try:
        from services.platform.macos_permissions import report
        states = report()
    except Exception as exc:
        return [('WARN', 'дозволи', f'не вдалося перевірити: {type(exc).__name__}')]
    result = []
    for name, state in states.items():
        level = {'granted': 'OK', 'denied': 'WARN'}.get(state, 'WARN')
        detail = state if state == 'granted' else f'{state}. {PERMISSION_HINTS[name]}'
        result.append((level, f'дозвіл {name}', detail))
    return result


def collect(which=shutil.which, run=subprocess.run):
    checks = [('OK', 'macOS', f'{platform.mac_ver()[0] or "?"} {platform.machine()}'),
              ('OK' if sys.version_info >= (3, 10) else 'FAIL', 'Python', sys.version.split()[0])]
    checks += _tools(which)
    checks.append(_ukrainian_voice(run))
    checks.append(_audio_input())
    checks += _permissions()
    return checks


def main() -> int:
    if platform.system() != 'Darwin':
        print('tools/macos_check.py призначений лише для macOS.')
        return 2
    checks = collect()
    for level, name, detail in checks:
        print(f'[{level:4}] {name}: {detail}')
    return int(any(level == 'FAIL' for level, _, _ in checks))


if __name__ == '__main__':
    raise SystemExit(main())
