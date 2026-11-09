from __future__ import annotations

import asyncio
from datetime import datetime

import psutil

from core.models import SkillResult
from services.platform import PlatformOperationError, resolve_platform


TIME_COMMANDS = {"котра година", "який час", "скажи час"}
DATE_COMMANDS = {"яка дата", "скажи дату", "який сьогодні день"}
SYSTEM_INFO_COMMANDS = {
    "навантаження процесора",
    "стан процесора",
    "стан cpu",
    "стан системи",
}


def can_handle(command, services):
    return (
        command in TIME_COMMANDS
        or command in DATE_COMMANDS
        or command in SYSTEM_INFO_COMMANDS
        or command in {
            "вимкни комп'ютер",
            "скасуй вимкнення",
            "заблокуй екран",
            "заверши роботу",
            "заверши роботу рейвена",
            "вимкни рейвена",
        }
    )


async def handle(command, context, services):
    now = datetime.now()
    if command in TIME_COMMANDS:
        return SkillResult(True, f"Зараз {now:%H:%M}.", {"command_type": "time"})
    if command in DATE_COMMANDS:
        return SkillResult(True, f"Сьогодні {now:%d.%m.%Y}.", {"command_type": "date"})
    if command in SYSTEM_INFO_COMMANDS:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
        ram = psutil.virtual_memory().percent
        return SkillResult(
            True,
            f"Навантаження процесора {cpu:.0f} відсотків, пам'яті {ram:.0f} відсотків.",
            {"command_type": "system_info"},
        )
    if command in {"заверши роботу", "заверши роботу рейвена", "вимкни рейвена"}:
        return SkillResult(
            True,
            "Завершую роботу Рейвена.",
            {"command_type": "assistant_shutdown", "shutdown_app": True},
        )
    if command == "скасуй вимкнення":
        completed = await asyncio.to_thread(resolve_platform().system_action, 'cancel_shutdown')
        response = (
            "Вимкнення скасовано."
            if completed
            else "Не вдалося скасувати вимкнення."
        )
        return SkillResult(True, response, {"command_type": "shutdown_cancel"})
    if command == "вимкни комп'ютер":
        if not await context.confirm("вимкнення комп'ютера через 60 секунд"):
            return SkillResult(True, "")
        completed = await asyncio.to_thread(resolve_platform().system_action, 'shutdown')
        response = (
            "Комп'ютер буде вимкнено через 60 секунд."
            if completed
            else "Windows не прийняла команду вимкнення."
        )
        return SkillResult(True, response, {"command_type": "shutdown"})
    if command == "заблокуй екран":
        if not await context.confirm("блокування екрана"):
            return SkillResult(True, "")
        locked = bool(await asyncio.to_thread(resolve_platform().system_action, 'lock'))
        return SkillResult(
            True,
            "Екран заблоковано." if locked else f"{_os_name()} не підтвердила блокування екрана.",
            {"command_type": "lock"},
        )
    return SkillResult(False)


# Model ACTIONs lock_screen and screenshot (user decision 07.11.2026): both keep the
# correlated confirmation. A screenshot stays on this computer; it is never sent to a model.
def _os_name():
    return "macOS" if getattr(resolve_platform(), "os", "") == "Darwin" else "Windows"


async def lock_screen(context):
    from core.confirmation import confirm_action
    cancelled = await confirm_action(context, f"блокування сеансу {_os_name()}", "Заблокувати екран")
    if cancelled is not None:
        return cancelled
    try:
        locked = bool(await asyncio.to_thread(resolve_platform().system_action, 'lock'))
    except PlatformOperationError as exc:
        return SkillResult(True, f"{exc} Екран не заблоковано.",
                           {"command_type": "lock", "accepted": False, "verified": False,
                            "success": False, "status": exc.status})
    return SkillResult(True, "Екран заблоковано." if locked else f"{_os_name()} не підтвердила блокування екрана.",
                       {"command_type": "lock", "accepted": True, "verified": locked,
                        "status": "verified" if locked else "unverified"})


def screenshot_folder():
    from pathlib import Path
    pictures = Path.home() / "Pictures"
    return (pictures if pictures.is_dir() else Path.home()) / "Raven"


def _save_screenshot(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if getattr(resolve_platform(), "os", "") == "Darwin":
        return resolve_platform().take_screenshot(path)  # screencapture; needs Screen Recording permission
    import pyautogui
    pyautogui.screenshot(str(path))
    return path.is_file() and path.stat().st_size > 0


async def save_screenshot(context):
    from core.confirmation import confirm_action
    folder = screenshot_folder()
    cancelled = await confirm_action(context, f"знімок усього екрана у файл PNG у теці {folder}; нікуди не надсилається",
                                     "Зробити знімок екрана")
    if cancelled is not None:
        return cancelled
    path = folder / f"screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        saved = await asyncio.to_thread(_save_screenshot, path)
    except PlatformOperationError as exc:
        return SkillResult(True, f"{exc} Знімок екрана не збережено.",
                           {"command_type": "screenshot", "accepted": False, "verified": False,
                            "success": False, "status": exc.status})
    except Exception:
        saved = False
    if not saved:
        return SkillResult(True, "Не вдалося зберегти знімок екрана.",
                           {"command_type": "screenshot", "accepted": True, "verified": False,
                            "success": False, "status": "unverified"})
    return SkillResult(True, f"Знімок екрана збережено: {path.name}, тека {folder}.",
                       {"command_type": "screenshot", "accepted": True, "verified": True, "status": "verified"})
