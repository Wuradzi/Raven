"""Fixes from the live test of 04.11.2026 (knowledge gap and knowledge base).

1. Model prose claiming it saved / forgot something is replaced: only exact
   phrases with confirmation change memory or the knowledge base.
2. «Не знаю цього напевно» is recorded as Raven's reply, so the model cannot
   later deny having said it (scored and unscored Groq paths).
3. «Забудь довідку про кафедру» finds the title «кафедра …» (word stems).
4. «Не шукай про нього» / «ні, не треба» are refusals in a confirmation;
   mixed or accepting replies are unchanged.
5. A phrase said twice in one utterance is parsed once.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from core.action_policy import KNOWLEDGE_GAP_NOTICE
from core.command_intent import CommandIntent
from core.confirmation import ConfirmationService
from core.models import SkillResult, TurnEnvelope
from core.natural_turn import MEMORY_CLAIM_NOTICE, NaturalTurn, TurnDecoder, memory_change_claim
from services.storage.knowledge_store import KnowledgeStore
from skills.memory.knowledge import parse_knowledge_request
from tests.processor_gate_matrix import build


class MemoryClaimTests(unittest.TestCase):
    def test_claims_of_saving_or_forgetting_are_detected(self):
        for text in ("Збережено до бази знань.", "Зрозумів, інформацію про кафедру кібербезпеки забуто.",
                     "Я запам’ятав цей факт.", "Додав довідку в базу знань", "Видалено запис з пам'яті."):
            with self.subTest(text=text):
                self.assertTrue(memory_change_claim(text))

    def test_ordinary_speech_is_not_a_claim(self):
        for text in ("Я забув, як це називається.", "Гарібальді народився в Ніцці.",
                     "Скажіть «Додай у базу знань», щоб зберегти довідку.", "Інформація про кафедру є на сайті."):
            with self.subTest(text=text):
                self.assertFalse(memory_change_claim(text))

    def test_decoder_replaces_claim_before_it_is_spoken(self):
        decoder = TurnDecoder()
        decoder.feed("CHAT\nЗбережено до бази знань.")
        self.assertEqual(decoder.finish().response, MEMORY_CLAIM_NOTICE)


class RefusalTests(unittest.TestCase):
    def test_natural_refusals(self):
        for text in ("Не шукай про нього.", "ні, не треба", "Не треба шукати", "не хочу"):
            with self.subTest(text=text):
                self.assertIs(ConfirmationService._decision(text), False)
                self.assertIs(ConfirmationService._decision(text, 0.5), False)
                self.assertIsNone(ConfirmationService._decision(text, 0.49))

    def test_mixed_and_accepting_replies_unchanged(self):
        for text in ("ні так", "так ні", "не так так", "Не знаю", "ні, давай так"):
            with self.subTest(text=text):
                self.assertIsNone(ConfirmationService._decision(text))
        self.assertIs(ConfirmationService._decision("Так."), True)


class KnowledgeMatchingTests(unittest.TestCase):
    def test_forget_topic_matches_word_stems(self):
        with tempfile.TemporaryDirectory() as temp:
            store = KnowledgeStore(Path(temp) / "kb.json")
            entry = store.add("кафедра кібербезпеки, корпус 7, аудиторія 412", "корпус 7, аудиторія 412")
            self.assertEqual(store.matching("кафедру"), [entry])
            self.assertEqual(store.matching("кафедру кібербезпеки"), [entry])
            self.assertEqual(store.matching("бібліотеку"), [])
            self.assertEqual(store.matching(""), [])

    def test_repeated_phrase_counts_once(self):
        self.assertEqual(parse_knowledge_request("Що є в базі знань? Що є в базі знань?"), ("list", "", ""))
        self.assertIsNone(parse_knowledge_request("Що є в базі знань? Онови базу знань."))


class NoticeHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.processor, _router, self.llm, _memory, _speaker = build("chat", True)
        self.processor.services["enabled_skills"] |= {"web"}
        self.llm.record_local_action = Mock()
        self.llm.converse = AsyncMock(return_value=NaturalTurn(
            "lookup", intent=CommandIntent("web_search", {"query": "Джузеппе Гарібальді"})))
        patcher = patch("core.processor.execute_intent",
                        AsyncMock(return_value=SkillResult(True, "Дію скасовано.", {"command_type": "cancelled"})))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_notice_is_recorded_on_both_paths(self):
        for groq in (False, True):
            with self.subTest(groq=groq):
                self.llm.record_local_action.reset_mock()
                text = "Хто такий Гарібальді?"
                envelope = (TurnEnvelope(turn_id="n", session_id="n", source="voice", text=text, transcript=text,
                                         stt_engine="groq", confidence=0, confidence_available=False,
                                         action_eligible=False) if groq else
                            TurnEnvelope(turn_id="n", session_id="n", source="text", text=text, transcript=text,
                                         stt_engine="text", confidence=1.0))
                await self.processor.process(envelope, AsyncMock(return_value=False))
                self.llm.record_local_action.assert_any_call(text, KNOWLEDGE_GAP_NOTICE)


if __name__ == "__main__":
    unittest.main()
