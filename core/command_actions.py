"""The only executor for model-proposed actions: validate, preview, confirm.

Read-only or easily reversible tools in UNCONFIRMED_TOOLS run without a voice «так»
(user decisions 04.11.2026 and 07.11.2026: media keys and «show desktop» too); every
tool that changes files, memory or the system keeps the correlated confirmation:
find_files, window changes except focus, lock_screen, screenshot. Each tool has one
explicit handler; a tool without one is refused, never routed to another tool.
"""
from __future__ import annotations

import asyncio
import logging

from core.command_intent import InvalidIntent, validate_intent
from core.models import SkillResult
from core.command_catalog import TOOL_SKILLS, command_help
from core.confirmation import confirm_action

UNCONFIRMED_TOOLS = frozenset({"web_search", "weather", "open_app", "media_control", "show_desktop"})
# Tools a clean, unscored Groq voice turn may propose (candidate path, then the same
# policy and executor). Damaged speech proposes nothing (TurnGate / TrustLevel).
UNSCORED_VOICE_TOOLS = frozenset({"open_app", "find_files", "weather", "web_search", "media_control",
                                  "show_desktop", "window_control", "lock_screen", "screenshot"})
# Roadmap phase 4 (E267: file search ≈ 15 s, web 10–30 s in silence). One spoken status,
# only by voice and only for the work itself, never while a confirmation is awaited.
LONG_ACTION_STATUS = "Ще шукаю."
LONG_ACTION_STATUS_SECONDS = 7.0


async def with_status(work, context, delay=None):
    """Await work; say LONG_ACTION_STATUS once when it runs longer than delay."""
    delay = LONG_ACTION_STATUS_SECONDS if delay is None else delay
    speaker = context.services.get("speaker")
    if context.source != "voice" or speaker is None or delay <= 0:
        return await work
    task = asyncio.ensure_future(work)
    try:
        await asyncio.wait({task}, timeout=delay)
        if not task.done():
            # A status, not a result: the tool's own reply still says what was found.
            await speaker.say(LONG_ACTION_STATUS)
        return await task
    finally:
        if not task.done():
            task.cancel()


async def execute_intent(intent, context):
    """Legacy SkillResult API with an explicit, non-inferred evidence boundary."""
    result = await _execute_intent(intent, context)
    evidence = result.execution
    logging.getLogger(__name__).debug(
        'Execution result turn_id=%s status=%s accepted=%s success=%s verified=%s',
        context.turn_id, evidence.status.value, evidence.accepted, evidence.success, evidence.verified)
    return result


async def _execute_intent(intent, context):
    # Treat even an adapter's CommandIntent object as untrusted at this boundary.
    try:
        intent = validate_intent({"tool": intent.tool, "arguments": intent.arguments})
    except (InvalidIntent, AttributeError):
        return SkillResult(True, "Не вдалося надійно розібрати команду. Дію не виконано.",
                           {"command_type": "interpretation_invalid"})
    tool, args = intent.tool, intent.arguments
    if tool not in TOOL_SKILLS:
        return SkillResult(True, "Не визначив підтримувану дію. Уточніть запит. Доступні команди: " + command_help(context.services.get("enabled_skills", set())) + ".",
                           {"command_type": "interpretation_unsupported"})
    if TOOL_SKILLS[tool] not in context.services.get("enabled_skills", set()):
        return SkillResult(True, "Потрібну навичку вимкнено або вона недоступна на цій платформі.",
                           {"command_type": "interpretation_unavailable"})
    handler = _HANDLERS.get(tool)
    if handler is None:
        return SkillResult(True, "Для цієї дії немає локального виконавця. Нічого не виконано.",
                           {"command_type": "interpretation_unsupported", "success": False})
    # No shell, eval, dynamic code, arbitrary URLs/paths, or router re-entry.
    return await handler(args, context)


async def _prepare_workplace(args, context):
    # The resolved local plan provides the single confirmation, not the model.
    return await context.services["workplace"].run("режим робота", context)


async def _workplace_followup(command, context):
    return await context.services["workplace"].followup(command, context)


async def _window_control(args, context):
    from skills.windows.skill import control_window
    return await control_window(args["name"], args["action"], context)


async def _find_files(args, context):
    extension = args['extension'] or 'будь-який'
    cancelled = await confirm_action(
        context,
        f"пошук у дозволених каталогах файлів із назвою, що містить «{args['query']}» (тип: {extension})",
        f"Знайти файли «{args['query']}», тип {extension}")
    if cancelled is not None:
        return cancelled
    from skills.files.skill import find_files
    return await with_status(find_files(args["query"], args["extension"], context), context)


# UNCONFIRMED_TOOLS: validated arguments, no confirmation.
async def _open_app(args, context):
    from skills.apps.skill import open_application
    return await open_application(args["name"], context, context.services)


async def _weather(args, context):
    from skills.web.skill import get_weather
    return await get_weather(args["city"], args["period"], context.services)


async def _web_search(args, context):
    from skills.web.skill import search_web
    return await with_status(search_web(args["query"], context.services), context)


async def _media_control(args, context):
    from skills.media.skill import press_media
    return await press_media(args["action"], args["steps"])


async def _show_desktop(args, context):
    from skills.windows.skill import show_desktop
    return await show_desktop(context)


# Confirmed inside their skills (correlated confirmation).
async def _lock_screen(args, context):
    from skills.system.skill import lock_screen
    return await lock_screen(context)


async def _screenshot(args, context):
    from skills.system.skill import save_screenshot
    return await save_screenshot(context)


_HANDLERS = {
    "prepare_workplace": _prepare_workplace,
    "workplace_status": lambda args, context: _workplace_followup("статус завдання", context),
    "workplace_retry": lambda args, context: _workplace_followup("повтори невдалий крок", context),
    "window_control": _window_control,
    "find_files": _find_files,
    "open_app": _open_app,
    "weather": _weather,
    "web_search": _web_search,
    "media_control": _media_control,
    "show_desktop": _show_desktop,
    "lock_screen": _lock_screen,
    "screenshot": _screenshot,
}
