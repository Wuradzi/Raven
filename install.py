from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Інсталятор Raven")
    parser.add_argument(
        "--profile",
        choices=("desktop", "edge", "raspberry_pi"),
        default="desktop",
    )
    parser.add_argument("--with-extra-llm", action="store_true")
    parser.add_argument("--with-vision", action="store_true")
    parser.add_argument('--with-voice', action='store_true', help='Install experimental edge runtime, not model weights')
    parser.add_argument('--check', action='store_true', help='Read-only Python/core dependency readiness; no installations')
    return parser.parse_args()


def _install(root: Path, requirement_file: str) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(root / requirement_file),
        ],
        check=True,
    )


def _platform_requirements() -> str:
    system = platform.system()
    if system == "Windows":
        return "requirements_win.txt"
    if system == "Darwin":
        return "requirements_mac.txt"
    return "requirements_linux.txt"


def _print_check_hint() -> None:
    if platform.system() == 'Darwin':
        print('macOS: python tools/macos_check.py — системні утиліти, голос і дозволи (лише читання).')


def _print_platform_notes() -> None:
    system = platform.system()
    if system == "Windows":
        print("Встановіть RHVoice та український голос Volodymyr.")
    elif system == "Darwin":
        print("macOS: див. docs/MACOS_SETUP.md (Homebrew portaudio, голос Lesya, дозволи). "
              "Рекомендовано scripts/install_macos.sh. Нічого з системних налаштувань не змінено.")
    else:
        print(
            "Debian-family: Python venv + libportaudio2 are system prerequisites for audio. "
            "Install them explicitly as administrator if needed. No sudo or OS changes were run. "
            "Linux TTS/window/power control NOT_IMPLEMENTED; no desktop required."
        )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 10):
        print('Python 3.10+ required; reference Windows uses 3.12.')
        return 2
    if args.profile == 'raspberry_pi' and (platform.system() != 'Linux' or platform.machine().lower() not in ('aarch64', 'arm64')):
        print('raspberry_pi bootstrap requires ARM64 Linux (architecture is not proof of Pi hardware).')
        return 2
    if args.check:
        modules = ('dotenv', 'psutil', 'rapidfuzz', 'cryptography', 'httpx', 'numpy', 'vosk', 'sounddevice',
                   'ddgs', 'dateparser', 'send2trash', 'google.genai', 'groq')
        missing = []
        for module in modules:
            try:
                present = find_spec(module) is not None
            except ModuleNotFoundError:
                present = False
            print(f'{module}: {"installed" if present else "missing"}')
            if not present:
                missing.append(module)
        _print_check_hint()
        print('Next: python main.py --doctor (config, paths, models, audio runtime; no downloads).')
        return int(bool(missing))
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    # One platform file installs every module (core, Groq, desktop/pyobjc, Qt, Whisper, sherpa-onnx, vision, extra LLMs).
    _install(root, _platform_requirements())

    _print_platform_notes()

    print("Optional voice setup: python tools/download_vosk_model.py (explicit download)")
    if args.profile == "desktop":
        print("Primary STT: Groq (set GROQ_API_KEY in .env, stt.backend=groq). Local fallback: python tools/download_whisper_model.py")
    else:
        print('STT: виберіть stt.profile=edge; ONNX файли підготуйте за docs/STT_BACKENDS.md (експериментально).')
    print("Next: python main.py --doctor; python main.py --text-only (no audio models required).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
