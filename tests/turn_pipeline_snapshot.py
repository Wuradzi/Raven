"""Turn pipeline snapshot for roadmap phase 1: trust x model reply -> observable outcome.

tests/processor_gate_matrix.py shows which path a turn reaches, but its model always
answers plain CHAT. This snapshot varies the model reply (CHAT, a false action claim,
ACTION, LOOKUP, PROPOSAL, ...) for every trust level, so a refactor of the two turn
paths shows any change in what is executed, confirmed or spoken.

Regenerate only after a reviewed, intended behaviour change:
    .venv\\Scripts\\python.exe -m tests.turn_pipeline_snapshot --write
Not a test module by itself (see tests/test_turn_pipeline_snapshot.py).
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.command_intent import CommandIntent
from core.models import SkillResult, TurnEnvelope
from core.natural_turn import NaturalTurn
from tests.processor_gate_matrix import TRUST, build

GOLDEN = Path(__file__).with_name("turn_pipeline_snapshot.json")

TRUSTS = dict(TRUST, text={},
              # Groq text that is only linguistically cut off (lexical fragment): not truncated or unreliable.
              unscored_cut={"stt_engine": "groq", "confidence": 0.0, "confidence_available": False,
                            "utterance_incomplete": True, "clarification_required": True,
                            "action_eligible": False})
TEXTS = {
    "open": "Відкрий блокнот",
    "find": "Знайди файл звіт",
    "chat": "Розкажи про Марс",
    "volume": "Зроби тихіше",
    "close": "Закрий вікно Блокнот",
    "paint": "Відкрий Paint",  # an exact installed-app name (local shortcut / Groq local_exact)
}
EXACT_APPS = {"paint"}
REPLIES = {
    "chat": NaturalTurn("chat", "Марс — четверта планета."),
    "claim_volume": NaturalTurn("chat", "Зменшую гучність."),
    "claim_open": NaturalTurn("chat", "Відкриваю блокнот."),
    "claim_memory": NaturalTurn("chat", "Запам'ятав ваше ім'я."),
    "token": NaturalTurn("chat", "UNSUPPORTED"),
    # Live test 07.11.2026 (E279): a headerless reply led by a protocol line was spoken whole.
    "token_lead": NaturalTurn("chat", "UNSUPPORTED\nЦю дію не підтримує поточний каталог команд."),
    "token_lead_claim": NaturalTurn("chat", "CHAT\nЗменшую гучність."),
    "clarify": NaturalTurn("clarify", "Яку програму відкрити?"),
    "action_open": NaturalTurn("action", intent=CommandIntent("open_app", {"name": "Notepad"})),
    "action_find": NaturalTurn("action", intent=CommandIntent("find_files", {"query": "звіт", "extension": ""})),
    "action_window": NaturalTurn("action", intent=CommandIntent("window_control",
                                                                {"name": "Notepad", "action": "minimize"})),
    # Computer control (07.11.2026).
    "action_media": NaturalTurn("action", intent=CommandIntent("media_control",
                                                               {"action": "volume_down", "steps": "3"})),
    "action_close": NaturalTurn("action", intent=CommandIntent("window_control",
                                                               {"name": "Notepad", "action": "close"})),
    "action_desktop": NaturalTurn("action", intent=CommandIntent("show_desktop", {})),
    "action_lock": NaturalTurn("action", intent=CommandIntent("lock_screen", {})),
    "lookup": NaturalTurn("lookup", intent=CommandIntent("web_search", {"query": "Марс"})),
    "proposal": NaturalTurn("proposal", intent=CommandIntent("open_app", {"name": "Notepad"})),
    "unavailable": NaturalTurn("unavailable", "Мовна модель недоступна. Нічого не виконано."),
    # Model failure after part of the answer was already spoken (see STREAM_FIRST).
    "unavailable_streamed": NaturalTurn("unavailable", "Перервався зв'язок із мовною моделлю. Нічого не виконано."),
}
STREAM_FIRST = {"unavailable_streamed": "Перша частина відповіді. "}
# Replies used only in multi-turn sequences.
SEQUENCE_REPLIES = {
    "clarify_app": NaturalTurn("clarify", "Яку програму відкрити?"),
    "chat_which_app": NaturalTurn("chat", "Яку саме програму відкрити?"),
    "action_telegram": NaturalTurn("action", intent=CommandIntent("open_app", {"name": "Telegram"})),
    "clarify_file": NaturalTurn("clarify", "Як називається файл?"),
    "clarify_window": NaturalTurn("clarify", "Яке вікно закрити?"),
    "chat_offer": NaturalTurn("chat", "Марс — четверта планета. Пошукати новини про Марс?"),
    "action_search": NaturalTurn("action", intent=CommandIntent("web_search", {"query": "новини про Марс"})),
}
# Multi-turn flows where the second turn depends on state left by the first (scoped
# clarification, Groq app-name slot, pause, «Команда» prefix, Raven's own offer).
SEQUENCES = {
    "scoped_app_clarify": (("А ти можеш мені відкрити програму?", "clarify_app"), ("Telegram", "action_telegram")),
    "scoped_app_chat": (("Відкрий програму", "chat_which_app"), ("Telegram", "action_telegram")),
    "scoped_app_yes": (("Відкрий програму", "clarify_app"), ("Так", "action_telegram")),
    "scoped_find": (("Знайди файл", "clarify_file"), ("звіт", "action_find")),
    "scoped_close": (("Закрий вікно", "clarify_window"), ("Блокнот", "action_close")),
    "pause_then_chat": (("пауза", "chat"), ("Розкажи про Марс", "chat")),
    "pause_resume": (("пауза", "chat"), ("продовжуй", "chat"), ("Розкажи про Марс", "chat")),
    "command_prefix": (("Команда, відкрий блокнот", "action_open"),),
    "offer_yes": (("Розкажи про Марс", "chat_offer"), ("Так, пошукай", "action_search")),
}
NATURAL = (False, True)


def envelope(text: str, trust: str, turn_id: str = "snap") -> TurnEnvelope:
    if trust == "text":
        return TurnEnvelope(turn_id=turn_id, session_id="snap", source="text", text=text, transcript=text,
                            stt_engine="text", confidence=1.0)
    base = TurnEnvelope(turn_id=turn_id, session_id="snap", source="voice", text=text, transcript=text,
                        stt_engine="vosk", confidence=0.95)
    return replace(base, **TRUSTS[trust])


def _reply(kind):
    return REPLIES.get(kind) or SEQUENCE_REPLIES[kind]


async def run_steps(trust: str, natural: bool, steps) -> list:
    """One processor for all steps; one observation row per turn."""
    processor, router, llm, _memory, speaker = build("chat", natural)
    processor.services["apps"] = SimpleNamespace(has_exact_name=lambda name: name.casefold() in EXACT_APPS)
    llm.rewrite_last_answer = lambda answer: None
    llm.record_local_action = lambda *args: None
    rows = []
    for index, (text, reply_kind) in enumerate(steps):
        reply = _reply(reply_kind)
        streamed, prompts, executed = [], [], []

        async def converse(*args, on_chunk=None, reply=reply, reply_kind=reply_kind, streamed=streamed,
                           prompts=prompts, **kwargs):
            streamed.append(on_chunk is not None)
            prompts.append(args[0][:90] if args else None)
            if on_chunk is not None and reply_kind in STREAM_FIRST:
                await on_chunk(STREAM_FIRST[reply_kind])
            elif on_chunk is not None and reply.kind == "chat":
                await on_chunk(reply.response)  # the real ChatPreview streams CHAT prose only
            return reply

        async def execute(intent, context, executed=executed):
            executed.append(f"{intent.tool}:{','.join(f'{k}={v}' for k, v in sorted(intent.arguments.items()))}")
            return SkillResult(True, f"executed {intent.tool}", {"command_type": f"executed_{intent.tool}"})

        llm.converse = converse
        router.calls.clear()
        chat_before = llm.chat.await_count
        said_before = speaker.say.await_count
        confirm = AsyncMock(return_value=True)
        with patch("core.processor.execute_intent", execute):
            result = await asyncio.wait_for(
                processor.process(envelope(text, trust, f"snap-{index}"), confirm), timeout=5)
        pending = processor._natural_pending
        rows.append([
            result.data.get("command_type", ""),
            result.response[:60],
            [entry.split(":")[0] for entry in executed],
            confirm.await_count > 0,
            [call.args[0][:60] for call in speaker.say.await_args_list[said_before:]],
            streamed,
            bool(router.calls),
            llm.chat.await_count > chat_before,
            pending.get("tool") if isinstance(pending, dict) else None,
            executed,
            prompts,
        ])
    return rows


async def observe(trust: str, natural: bool, text_kind: str, reply_kind: str) -> list:
    return (await run_steps(trust, natural, ((TEXTS[text_kind], reply_kind),)))[0]


FIELDS = ("command_type", "response", "executed", "confirm_asked", "spoken", "converse_streamed",
          "routed", "chat_llm", "pending_tool", "executed_arguments", "model_text")


def cases():
    for trust in TRUSTS:
        for natural in NATURAL:
            for text_kind in TEXTS:
                for reply_kind in REPLIES:
                    yield trust, natural, text_kind, reply_kind


def key(trust, natural, text_kind, reply_kind) -> str:
    return f"{trust}|natural={natural}|{text_kind}|{reply_kind}"


async def snapshot() -> dict:
    data = {key(*case): await observe(*case) for case in cases()}
    for trust in TRUSTS:
        for natural in NATURAL:
            for name, steps in SEQUENCES.items():
                for index, row in enumerate(await run_steps(trust, natural, steps)):
                    data[f"{trust}|natural={natural}|seq:{name}#{index}"] = row
    return data


def main(argv) -> int:
    data = asyncio.run(snapshot())
    if "--write" in argv:
        GOLDEN.write_text(json.dumps({"fields": FIELDS, "cases": data}, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print(f"written {len(data)} cases to {GOLDEN.name}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
