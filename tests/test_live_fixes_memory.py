"""Fixes from the live test of 04.11.2026 (knowledge gap and knowledge base).

1. Model prose claiming it saved / forgot something is replaced: only exact
   phrases with confirmation change memory or the knowledge base.
2. The knowledge-gap notice is recorded as Raven's reply, so the model cannot
   later deny having said it (scored and unscored Groq paths). Since 04.11.2026
   the notice is «Шукаю в інтернеті «…».» and the search needs no «так».
3. «Забудь довідку про кафедру» finds the title «кафедра …» (word stems).
4. «Не шукай про нього» / «ні, не треба» are refusals in a confirmation;
   mixed or accepting replies are unchanged.
5. A phrase said twice in one utterance is parsed once.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.command_intent import CommandIntent
from core.confirmation import ConfirmationService
from core.models import RecognitionResult, SkillResult, TurnEnvelope
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
                # Since 04.11.2026 the search runs without a «так»; its notice is kept in history.
                self.llm.record_local_action.assert_any_call(text, "Шукаю в інтернеті «Джузеппе Гарібальді».")


class SecondLiveTestFixes(unittest.TestCase):
    """Live test of 04.11.2026, second session (E262)."""

    def test_data_update_claims_are_replaced(self):
        for text in ("Зрозумів, оновив дані.", "Зрозумів, оновив базу даних.", "Дані збережено."):
            with self.subTest(text=text):
                self.assertTrue(memory_change_claim(text))
        self.assertFalse(memory_change_claim("Дані НБУ оновлюються щодня."))
        self.assertFalse(memory_change_claim("Я забув дати відповідь."))

    @staticmethod
    def _groq(text, **flags):
        return RecognitionResult(text, 0.0, engine="groq", confidence_available=False, **flags)

    def test_lexical_fragment_may_refuse_but_never_approve(self):
        refusal = self._groq("Не хочу.", utterance_incomplete=True, incomplete=True)
        confidence = ConfirmationService.voice_confidence(refusal)
        self.assertIsNone(confidence)
        self.assertIs(ConfirmationService._decision(refusal.text, confidence), False)
        for text in ("Так, але", "Давай, і"):
            with self.subTest(text=text):
                fragment = self._groq(text, utterance_incomplete=True, incomplete=True)
                confidence = ConfirmationService.voice_confidence(fragment)
                self.assertIsNone(ConfirmationService._decision(fragment.text, confidence))

    def test_damaged_audio_never_refuses_or_approves(self):
        for flags in ({"recognition_unreliable": True}, {"capture_truncated": True, "incomplete": True}):
            with self.subTest(flags=flags):
                result = self._groq("Не хочу.", **flags)
                confidence = ConfirmationService.voice_confidence(result)
                self.assertIsNone(ConfirmationService._decision(result.text, confidence))

    def test_davai_accepts_only_alone(self):
        self.assertIs(ConfirmationService._decision("Давай!"), True)
        self.assertIs(ConfirmationService._decision("Давай, давай."), True)
        self.assertIsNone(ConfirmationService._decision("Давай!", 0.79))
        for text in ("не давай", "давай не будемо", "ні, давай так", "Не хочу, давай"):
            with self.subTest(text=text):
                self.assertIsNone(ConfirmationService._decision(text))


class ThirdLiveTestFixes(unittest.TestCase):
    """Live test of 04.11.2026, repeat session (E264)."""

    def test_source_question_accepts_a_leading_conjunction(self):
        from core.processor import KNOWLEDGE_SOURCE_QUESTION
        for text in ("Звідки ти це знаєш?", "І звідки ти це знаєш?", "а звідки це", "Ну звідки ти знаєш"):
            with self.subTest(text=text):
                self.assertTrue(KNOWLEDGE_SOURCE_QUESTION.fullmatch(text))
        self.assertFalse(KNOWLEDGE_SOURCE_QUESTION.fullmatch("Звідки ти це знаєш і що це означає"))

    def test_database_wording_lists_but_never_changes(self):
        for text in ("Що є в базі даних?", "Що є в твоїй базі даних?", "Покажи базу даних"):
            with self.subTest(text=text):
                self.assertEqual(parse_knowledge_request(text), ("list", "", ""))
        for text in ("Онови базу даних", "Очисти базу даних", "Очистіть базу даних"):
            with self.subTest(text=text):
                self.assertIsNone(parse_knowledge_request(text))

    def test_polite_plural_forms_still_need_confirmation_phrases(self):
        self.assertEqual(parse_knowledge_request("Забудьте довідку про кафедру"), ("forget", "кафедру", ""))
        self.assertEqual(parse_knowledge_request("Видаліть довідку про кафедру"), ("forget", "кафедру", ""))
        self.assertEqual(parse_knowledge_request("Оновіть базу знань"), ("update", "", ""))
        self.assertEqual(parse_knowledge_request("Очистіть базу знань"), ("clear", "", ""))

    def test_withheld_files_are_announced_to_the_model_as_a_count_only(self):
        from services.llm.manager import KNOWLEDGE_CONTEXT_RULES, append_knowledge
        self.assertIn("withheld_file_entries", KNOWLEDGE_CONTEXT_RULES)
        messages = [{"role": "system", "content": "s"}]
        append_knowledge(messages, [{"withheld_file_entries": 2}])
        self.assertIn("withheld_file_entries", messages[-1]["content"])


class AssistantModeTests(unittest.IsolatedAsyncioTestCase):
    """Variant A of the project assessment (04.11.2026): less friction, faster feel."""

    def test_misheard_whole_commands(self):
        for text in ("О нову базу знань", "Онове базу знань.", "О нове базу знань!", "У нову базу знань"):
            with self.subTest(text=text):
                self.assertEqual(parse_knowledge_request(text), ("update", "", ""))
        for text in ("Що я в базі знань?", "Що місці в базі даних?", "Що зараз є в базі знань?"):
            with self.subTest(text=text):
                self.assertEqual(parse_knowledge_request(text), ("list", "", ""))
        for text in ("Онови базу даних", "Очисти базу даних", "Додай мені нову базу знань про космос",
                     "Розкажи, що ти думаєш про базу знань"):
            with self.subTest(text=text):
                self.assertIsNone(parse_knowledge_request(text))

    def test_yes_synonyms_stay_unknown(self):
        # J57 kept: only «давай» was added (04.11.2026); other synonyms need the user's decision.
        for text in ("Ага.", "Звісно!", "Авжеж"):
            with self.subTest(text=text):
                self.assertIsNone(ConfirmationService._decision(text))

    async def test_filler_speaks_once_only_when_the_model_is_silent(self):
        from core.processor import FILLER_PHRASE, _FirstPhraseFiller
        speaker = SimpleNamespace(say=AsyncMock())
        silent = _FirstPhraseFiller(speaker, 0.01)
        await asyncio.sleep(0.05)
        silent.stop()
        speaker.say.assert_awaited_once_with(FILLER_PHRASE)
        speaker.say.reset_mock()
        quick = _FirstPhraseFiller(speaker, 0.05)
        quick.heard()
        await asyncio.sleep(0.08)
        quick.stop()
        disabled = _FirstPhraseFiller(speaker, 0)
        disabled.stop()
        speaker.say.assert_not_awaited()

    async def test_filler_is_voice_only_and_off_without_setting(self):
        processor, _router, _llm, _memory, _speaker = build("chat", True)
        self.assertIsNone(processor._filler("voice")._task)  # fixtures have no llm_filler_seconds
        processor.settings.llm_filler_seconds = 2.5
        self.assertIsNone(processor._filler("text")._task)
        voice = processor._filler("voice")
        self.assertIsNotNone(voice._task)
        voice.stop()

    def test_filler_setting_is_validated(self):
        from config import ConfigError, default_config, validate_config
        config = default_config()
        self.assertEqual(config["llm"]["filler_seconds"], 2.5)
        for bad in (-1, 31, True, "2"):
            with self.subTest(value=bad):
                config["llm"]["filler_seconds"] = bad
                with self.assertRaises(ConfigError):
                    validate_config(config)


if __name__ == "__main__":
    unittest.main()
