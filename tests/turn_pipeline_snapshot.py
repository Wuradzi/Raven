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
from unittest.mock import AsyncMock, patch

from core.command_intent import CommandIntent
from core.models import SkillResult, TurnEnvelope
from core.natural_turn import NaturalTurn
from tests.processor_gate_matrix import TRUST, build

GOLDEN = Path(__file__).with_name("turn_pipeline_snapshot.json")

TRUSTS = dict(TRUST, text={})
TEXTS = {
    "open": "Відкрий блокнот",
    "find": "Знайди файл звіт",
    "chat": "Розкажи про Марс",
}
REPLIES = {
    "chat": NaturalTurn("chat", "Марс — четверта планета."),
    "claim_volume": NaturalTurn("chat", "Зменшую гучність."),
    "claim_open": NaturalTurn("chat", "Відкриваю блокнот."),
    "claim_memory": NaturalTurn("chat", "Запам'ятав ваше ім'я."),
    "token": NaturalTurn("chat", "UNSUPPORTED"),
    "clarify": NaturalTurn("clarify", "Яку програму відкрити?"),
    "action_open": NaturalTurn("action", intent=CommandIntent("open_app", {"name": "Notepad"})),
    "action_find": NaturalTurn("action", intent=CommandIntent("find_files", {"query": "звіт", "extension": ""})),
    "action_window": NaturalTurn("action", intent=CommandIntent("window_control",
                                                                {"name": "Notepad", "action": "minimize"})),
    "lookup": NaturalTurn("lookup", intent=CommandIntent("web_search", {"query": "Марс"})),
    "proposal": NaturalTurn("proposal", intent=CommandIntent("open_app", {"name": "Notepad"})),
    "unavailable": NaturalTurn("unavailable", "Мовна модель недоступна. Нічого не виконано."),
}
NATURAL = (False, True)


def envelope(text: str, trust: str) -> TurnEnvelope:
    if trust == "text":
        return TurnEnvelope(turn_id="snap", session_id="snap", source="text", text=text, transcript=text,
                            stt_engine="text", confidence=1.0)
    base = TurnEnvelope(turn_id="snap", session_id="snap", source="voice", text=text, transcript=text,
                        stt_engine="vosk", confidence=0.95)
    return replace(base, **TRUSTS[trust])


async def observe(trust: str, natural: bool, text_kind: str, reply_kind: str) -> list:
    processor, router, llm, _memory, speaker = build("chat", natural)
    reply = REPLIES[reply_kind]
    streamed = []

    async def converse(*args, on_chunk=None, **kwargs):
        streamed.append(on_chunk is not None)
        if on_chunk is not None and reply.kind == "chat":
            await on_chunk(reply.response)  # the real ChatPreview streams CHAT prose only
        return reply

    llm.converse = converse
    llm.rewrite_last_answer = lambda answer: None
    llm.record_local_action = lambda *args: None
    executed = []

    async def execute(intent, context):
        executed.append(intent.tool)
        return SkillResult(True, f"executed {intent.tool}", {"command_type": f"executed_{intent.tool}"})

    confirm = AsyncMock(return_value=True)
    with patch("core.processor.execute_intent", execute):
        result = await asyncio.wait_for(processor.process(envelope(TEXTS[text_kind], trust), confirm), timeout=5)
    pending = processor._natural_pending
    return [
        result.data.get("command_type", ""),
        result.response[:60],
        executed,
        confirm.await_count > 0,
        [call.args[0][:60] for call in speaker.say.await_args_list],
        streamed,
        bool(router.calls),
        llm.chat.await_count > 0,
        pending.get("tool") if isinstance(pending, dict) else None,
    ]


FIELDS = ("command_type", "response", "executed", "confirm_asked", "spoken", "converse_streamed",
          "routed", "chat_llm", "pending_tool")


def cases():
    for trust in TRUSTS:
        for natural in NATURAL:
            for text_kind in TEXTS:
                for reply_kind in REPLIES:
                    yield trust, natural, text_kind, reply_kind


def key(trust, natural, text_kind, reply_kind) -> str:
    return f"{trust}|natural={natural}|{text_kind}|{reply_kind}"


async def snapshot() -> dict:
    return {key(*case): await observe(*case) for case in cases()}


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
