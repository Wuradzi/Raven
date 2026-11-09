from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from config import ConfigError, load_settings
from core.console import console_print
from core.logging_setup import SessionLogging, configure_logging
from core.single_instance import SingleInstance


class ConsoleArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            console_print(message, end="")


def parse_args() -> argparse.Namespace:
    parser = ConsoleArgumentParser(description="Raven Voice Assistant")
    maintenance = parser.add_mutually_exclusive_group()
    for command in ('doctor', 'diagnostics', 'audio-test', 'models', 'smoke-live'):
        maintenance.add_argument('--' + command, action='store_true')
    parser.add_argument('--probe-audio', action='store_true', help='Explicit microphone recording during doctor')
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--web-ui", action="store_true", help="Локальний вебінтерфейс")
    parser.add_argument('--ui', choices=('native', 'web', 'headless'), default=None,
                        help='Інтерфейс: Qt Widgets (за замовчуванням), браузер або консоль')
    parser.add_argument("--web-port", type=int, default=0, help="Порт UI; 0 — вільний порт")
    parser.add_argument("--no-browser", action="store_true", help="Не відкривати UI автоматично")
    parser.add_argument(
        "--profile",
        choices=("balanced", "fast", "raspberry_pi"),
        help="Профіль продуктивності лише для цього запуску",
    )
    args = parser.parse_args()
    if args.web_ui and args.ui in {'native', 'headless'}:
        parser.error('--web-ui не поєднується з --ui native/headless')
    args.ui_explicit = args.ui is not None
    if args.ui is None:
        args.ui = _default_ui(args)
    args.web_ui = args.web_ui or args.ui == 'web'
    return args


def _default_ui(args: argparse.Namespace) -> str:
    """Native Qt window by default; console flows (--text-only, --setup, maintenance) stay in the CLI."""
    if args.web_ui:
        return 'web'
    console_flow = args.text_only or args.setup or any(
        getattr(args, name) for name in ('doctor', 'diagnostics', 'audio_test', 'models', 'smoke_live'))
    return 'headless' if console_flow else 'native'


def _native_unavailable_reason() -> str:
    """Why an implicitly selected native UI cannot start here (empty string: no known obstacle)."""
    if sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        return 'немає графічного дисплея (DISPLAY/WAYLAND_DISPLAY)'
    return ''


async def async_main(args=None, native_bridge=None) -> int:
    args = args or parse_args()
    if not 0 <= args.web_port <= 65535:
        console_print("Порт має бути від 0 до 65535.")
        return 2
    try:
        settings = load_settings(args.profile)
    except ConfigError as exc:
        print(f"[CONFIG] {exc}")
        console_print("Виправте config.json або відновіть його з резервної копії. Подробиці — у журналі сесії.")
        return 2

    configure_logging(settings)
    if args.setup or not settings.paths.config_file.exists():
        if native_bridge is not None:
            native_bridge.failure.emit('Спочатку виконайте python main.py --setup у терміналі, потім запустіть --ui native.')
            return 2
        from setup_wizard import run_first_start_wizard

        run_first_start_wizard(settings.paths.project_root)
        try:
            settings = load_settings(args.profile)
        except ConfigError as exc:
            print(f"[CONFIG] Налаштування не збережено: {exc}")
            console_print("Налаштування не збережено. Подробиці — у журналі сесії.")
            return 2

    configure_logging(settings)
    instance = SingleInstance(settings.paths.data_dir / "valera.lock")
    if not instance.acquire():
        console_print("Raven вже запущений.")
        return 2
    try:
        if native_bridge is not None and native_bridge.closing.is_set():
            return 0

        from core.metrics import MetricsCollector
        from core.performance import PerformanceRecorder

        performance = PerformanceRecorder(MetricsCollector(settings.paths.data_dir / "metrics.jsonl"))
        logging.getLogger("session.lifecycle").info("Performance run_id=%s", performance.run_id)
        with performance.span("startup.app_imports"):
            from core.app import RavenApp
        with performance.span("startup.app_construct"):
            app = RavenApp(settings, text_only=args.text_only, performance=performance)
        try:
            if native_bridge is not None:
                await native_bridge.attach(app)
            if args.web_ui:
                from core.web_ui import LocalWebUI
                from core.console import console_stream
                from core.logging_setup import register_secret

                app.web_ui = LocalWebUI(app, args.web_port)
                # Opening a web panel is not consent to start the microphone.
                app.microphone_enabled = False
                app._mic_ready.clear()
                if app.listener is not None:
                    app.listener.set_paused(True)
                url = await app.web_ui.start()
                register_secret(app.web_ui.token)
                # Do not persist the local bearer token in session logs.
                print(f"Вебінтерфейс цієї сесії: {url}", file=console_stream(), flush=True)
                if not args.no_browser:
                    import webbrowser

                    try:
                        await asyncio.to_thread(webbrowser.open, url)
                    except Exception:
                        console_print("Відкрийте посилання вебінтерфейсу вручну.")
            await app.run()
        finally:
            if app.web_ui is not None:
                await app.web_ui.close()
        return 0
    finally:
        instance.release()


def _load_native(args: argparse.Namespace):
    """Native runner, None to continue in the console, False when an explicit --ui native cannot start."""
    if getattr(args, 'ui', None) != 'native':
        return None
    explicit = getattr(args, 'ui_explicit', True)
    reason = '' if explicit else _native_unavailable_reason()
    if not reason:
        try:
            from native_ui.runtime import run_native
            return run_native
        except ImportError:
            reason = 'не встановлено PySide6 (python -m pip install -r requirements_<платформа>.txt: win/mac/linux)'
    if explicit:
        console_print('Native UI недоступний: ' + reason)
        return False
    console_print('Native UI недоступний: ' + reason + '. Запускаю консольний режим (--ui headless).')
    args.ui = 'headless'
    return None


def main() -> int:
    args = parse_args()
    if any(getattr(args, name) for name in ('doctor', 'diagnostics', 'audio_test', 'models', 'smoke_live')):
        from services.health.cli import run
        try:
            return run(args, Path(__file__).resolve().parent)
        except (Exception, KeyboardInterrupt) as exc:
            console_print(f'Перевірку не завершено: {type(exc).__name__}')
            return 1
    session = SessionLogging(Path(__file__).resolve().parent / "logs" / "sessions")
    try:
        with session:
            console_print(f"Журнал сесії: {session.path}")
            from core.log_retention import prune_quietly, prune_sessions
            prune_quietly(prune_sessions, Path(__file__).resolve().parent)
            try:
                run_native = _load_native(args)
                if run_native is None:
                    exit_code = asyncio.run(async_main(args))
                elif run_native is False:
                    return 2
                else:
                    exit_code = run_native(lambda bridge: async_main(args, bridge))
            except KeyboardInterrupt:
                logging.getLogger("session.lifecycle").info("Shutdown requested: Ctrl+C")
                console_print("\nRaven завершено.")
                return 0
            logging.getLogger("session.lifecycle").info("Application exit code: %s", exit_code)
            return exit_code
    except KeyboardInterrupt:
        console_print("\nRaven завершено.")
        return 0
    except Exception:
        # The context logged the traceback; do not dump it into the dialogue.
        if session.path.exists():
            console_print(f"Raven зупинено через помилку. Подробиці: {session.path}")
        else:
            console_print("Не вдалося створити журнал сесії. Перевірте доступ до logs/sessions і вільне місце.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    # Do not surface a normal shutdown as SystemExit in IDE debuggers.
    if exit_code:
        raise SystemExit(exit_code)
