from __future__ import annotations

import re

from core.models import SkillResult

NO_PASSWORDS = "Raven не зберігає й не показує паролі. Скористайтеся менеджером паролів."


def can_handle(command, services):
    return command.startswith((
        "запам'ятай",
        "забудь ",
        "очисти пам",
        "покажи всю пам",
        "що ти пам'ятаєш",
        "пароль від ",
    ))


async def handle(command, context, services):
    memory = services["memory"]
    raw_command = getattr(context, "raw_text", "") or command

    if command.startswith("очисти пам"):
        if await context.confirm("повне очищення локальної пам'яті"):
            memory.clear()
            return SkillResult(True, "Пам'ять очищено.", {"command_type": "memory_clear"})
        return SkillResult(True, "")

    if "покажи всю пам" in command or "що ти пам'ятаєш" in command:
        items = memory.all()
        if not items:
            return SkillResult(True, "Пам'ять порожня.")
        return SkillResult(
            True,
            "; ".join(f"{item['key']}: {item['value']}" for item in items[:10]),
            {"command_type": "memory_list"},
        )

    if command.startswith("забудь"):
        query = re.sub(r"^забудь\s+", "", command).strip()
        count = memory.forget(query)
        return SkillResult(True, f"Видалено записів: {count}.", {"command_type": "memory_forget"})

    if "пароль від" in command and not command.startswith("запам"):
        return SkillResult(True, NO_PASSWORDS, {"command_type": "secret_unsupported"})

    if command.startswith("запам"):
        content = re.sub(
            r"^запам['’]ятай[,\s]*",
            "",
            raw_command,
            flags=re.IGNORECASE,
        ).strip()
        if re.search(r"пароль від", content, re.IGNORECASE):
            return SkillResult(True, NO_PASSWORDS, {"command_type": "secret_unsupported"})

        match = re.match(
            r"(?:що\s+)?(.+?)\s+(?:це|:)\s+(.+)",
            content,
            re.IGNORECASE,
        )
        if not match:
            return SkillResult(True, "Скажіть: запам'ятай, що ключ це значення.")
        key, value = match.groups()
        memory.remember(key, value)
        return SkillResult(True, "Запам'ятав.", {"command_type": "memory_write"})

    return SkillResult(False)
