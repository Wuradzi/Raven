from __future__ import annotations

import re
import asyncio
from pathlib import Path

from core.command_intent import WINDOW_PHRASES
from core.models import SkillResult
from core.confirmation import confirm_action


def can_handle(command, services):
    return command.startswith((
        "закрий ",
        "згорни ",
        "розгорни ",
        "віднови ",
        "заверши ",
        "вбий ",
    ))


def target(command: str) -> str:
    return re.sub(
        r"^(закрий|згорни|розгорни|віднови|заверши|вбий)\s+(вікно|програму|процес)?\s*",
        "",
        command,
    ).strip()


async def control_window(name, action, context):
    controller = context.services["windows"]
    aliases = []
    apps = context.services.get("apps")
    if apps is not None:
        matches = await asyncio.wait_for(asyncio.to_thread(apps.find, name), 5)
        # Aliases help Ukrainian program names, but never choose between windows.
        if matches and matches[0].get("score", 0) >= 90:
            aliases = [matches[0]["name"], *matches[0].get("aliases", [])]
    targets = await asyncio.wait_for(asyncio.to_thread(controller.candidates, name, aliases), 5)
    if not targets:
        return SkillResult(True, "Такого доступного вікна не знайдено. Програма може бути закрита або лише у треї; нічого не запускаю.",
                           {"command_type": "window_" + action, "accepted": False, "success": False, "status": "not_found"})
    entries = [{**item, "action": action} for item in targets]
    if len(entries) > 1:
        return context.services["tasks"].offer("window", entries, command_type="window_choice")
    return await execute_window_target(entries[0], context)


# Switching to a window is easily reversible and runs without a «так» (user decision
# 07.11.2026); every other change, including close, keeps the correlated confirmation.
UNCONFIRMED_WINDOW_ACTIONS = frozenset({"focus"})
_DONE = {"maximize": "Вікно розгорнуто.", "minimize": "Вікно згорнуто.", "restore": "Вікно відновлено.",
         "focus": "Перейшов до вікна.", "close": "Вікно закрито."}


async def execute_window_target(target, context):
    if "windows" not in context.services.get("enabled_skills", set()):
        return SkillResult(True, "Керування вікнами вимкнено.", {"command_type": "window_unavailable", "success": False})
    action = target["action"]
    phrase = WINDOW_PHRASES[action]
    if action not in UNCONFIRMED_WINDOW_ACTIONS:
        details = f"{phrase} «{target['name']}», програма {Path(target['exe']).name}, PID {target['pid']}"
        cancelled = await confirm_action(context, details, f"{phrase} «{target['name'][:100]}»")
        if cancelled is not None:
            return cancelled
    try:
        evidence = await asyncio.wait_for(asyncio.to_thread(context.services["windows"].change_target, target, action), 4)
    except Exception:
        evidence = {"accepted": True, "verified": False, "status": "unverified"}
    data = {**evidence, "command_type": "window_" + action, "success": bool(evidence["verified"])}
    return SkillResult(True, _window_response(action, evidence), data)


def _window_response(action, evidence):
    if evidence["verified"]:
        if action == "close" and evidence.get("hidden"):
            return "Вікно сховано: програма могла лишитися працювати в треї."
        response = _DONE[action]
        if action not in {"minimize", "close"} and not evidence.get("foreground"):
            response += " Перехід на передній план не підтверджено."
        return response
    if not evidence["accepted"]:
        return "Вікно змінилося або вже недоступне. Повторіть запит; інше вікно не обираю."
    if action == "close":
        return "Вікно ще відкрите: програма може питати про збереження. Автоматично не повторюю."
    return "Стан вікна не вдалося підтвердити. Автоматично не повторюю дію."


async def show_desktop(context):
    """Win+D: hides or brings back all windows. Only a key press is confirmed, not the result."""
    import pyautogui
    await asyncio.to_thread(pyautogui.hotkey, "win", "d")
    return SkillResult(True, "Надіслав команду «показати робочий стіл»; повторна команда поверне вікна.",
                       {"command_type": "show_desktop", "accepted": True, "verified": False, "status": "sent"})


async def handle(command, context, services):
    name = target(command)
    if not name:
        return SkillResult(True, "Уточніть назву вікна або програми.")

    controller = services["windows"]
    for verb, action in (("згорни", "minimize"), ("розгорни", "maximize"), ("віднови", "restore")):
        if command.startswith(verb):
            return await control_window(name, action, context)
    if command.startswith("закрий"):
        count = controller.close(name)
        return SkillResult(True, f"Закрито вікон: {count}.", {"command_type": "window_close"})
    if command.startswith("заверши") or command.startswith("вбий"):
        if not await context.confirm(f"примусове завершення процесу {name}"):
            return SkillResult(True, "")
        count = controller.terminate_processes(name)
        return SkillResult(True, f"Завершено процесів: {count}.", {"command_type": "process_terminate"})
    return SkillResult(False)
