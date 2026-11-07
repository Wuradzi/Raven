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
# Live test 07.11.2026 (E279): a headerless reply «UNSUPPORTED\n<explanation>» was spoken whole.
# Leading token lines are protocol, not speech; the prose after them is screened as usual.
CONTROL_LINES = re.compile(r"^(?:\s*(?:unsupported|chat|clarify|action|proposal|followup|lookup)[.!?:]*[ \t]*(?:\r?\n|$))+",
                           re.I)

CHAT_MODE_NOTICE = "Я не виконую локальні дії в розмові. Скажіть: Команда, а потім назвіть дію."
NATURAL_NOTICE = "Дію ще не виконано. Для запуску потрібен погоджений план."


def claims_local_change(text: str) -> bool:
    """Model prose that claims a local action or a memory change, or a bare protocol token."""
    value = text.lower().strip()
    return bool(LOCAL_ACTION_CLAIM.match(value) or CONTROL_TOKEN.match(value) or memory_change_claim(text))


def screen_part(text: str, notice: str = NATURAL_NOTICE) -> str:
    """One streamed piece: '' for a protocol-only line (the caller skips it), else screen().

    The whole reply still gets `notice` when nothing speakable was streamed.
    """
    if not CONTROL_LINES.sub("", text.strip("*#` ")).strip("*#` \r\n"):
        return ""
    return screen(text, notice)


def screen(text: str, notice: str = NATURAL_NOTICE) -> str:
    """Speakable text without leading protocol lines or Markdown edges, or `notice` when
    nothing speakable is left or the model claims what only local code may do."""
    text = CONTROL_LINES.sub("", text.strip("*#` ")).strip("*#` \r\n")
    return notice if not text or claims_local_change(text) else text
