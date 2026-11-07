"""One screen for model prose before it is spoken or stored (roadmap phase 1, step 3).

Model text is never an execution receipt. A sentence that claims a local action or a
memory change, or a bare protocol token, is replaced by a fixed local notice. Before
phase 1 the unscored Groq voice path spoke such sentences unscreened (snapshot
tests/turn_pipeline_snapshot.json): «Зменшую гучність.» and «UNSUPPORTED» reached TTS.

Two notices with different content stay separate: plain chat mode runs no local
actions at all; the natural mode runs them only through a confirmed local plan.
"""
from __future__ import annotations

import re

from core.natural_turn import memory_change_claim

LOCAL_ACTION_CLAIM = re.compile(
    r"^(?:відкриваю|відкрив|відкрила|запускаю|запустив|запустила|"
    r"закриваю|закрив|закрила|вимикаю|вимкнув|вимкнула|вмикаю|увімкнув|увімкнула|"
    # Live test 06.11.2026: «Зменшую гучність.» was spoken although nothing ran.
    r"зменшую|зменшив|зменшила|збільшую|збільшив|збільшила|перемикаю|перемкнув|перемкнула|"
    r"налаштовую|налаштував|налаштувала)\b"
)
# A bare protocol token is never a reply (live test 06.11.2026: «UNSUPPORTED» was spoken).
CONTROL_TOKEN = re.compile(r"^(?:unsupported|chat|clarify|action|proposal|followup|lookup)[.!?]*$", re.I)

CHAT_MODE_NOTICE = "Я не виконую локальні дії в розмові. Скажіть: Команда, а потім назвіть дію."
NATURAL_NOTICE = "Дію ще не виконано. Для запуску потрібен погоджений план."


def claims_local_change(text: str) -> bool:
    """Model prose that claims a local action or a memory change, or a bare protocol token."""
    value = text.lower().strip()
    return bool(LOCAL_ACTION_CLAIM.match(value) or CONTROL_TOKEN.match(value) or memory_change_claim(text))


def screen(text: str, notice: str = NATURAL_NOTICE) -> str:
    """Markdown-stripped text, or `notice` when the model claims what only local code may do."""
    text = text.strip("*#` ")
    return notice if claims_local_change(text) else text
