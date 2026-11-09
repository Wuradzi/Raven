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


class PhaseSevenMemoryTests(unittest.IsolatedAsyncioTestCase):
    """Roadmap phase 7 (06.11.2026): memory and knowledge-base defects from live tests."""

    def setUp(self):
        from services.storage.memory_store import MemoryStore
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = MemoryStore(Path(self.temp.name) / "memory.json")
        self.fact = self.memory.remember("кафедра кібербезпеки", "корпус 7, аудиторія 412", category="user_explicit")
        self.processor, _router, self.llm, _memory, _speaker = build("chat", True)
        self.processor.services["memory"] = self.memory

    async def test_source_of_a_personal_fact_is_answered_locally(self):
        self.processor._last_memory_ids = [self.fact["id"]]
        self.processor._cache_reply("Кафедра кібербезпеки — у корпусі 7, аудиторія 412.")
        result = self.processor._knowledge_source_reply("Звідки ти це знаєш?")
        self.assertIn("З вашої особистої пам'яті", result.response)
        self.assertIn("корпус 7, аудиторія 412", result.response)
        self.assertEqual(result.data["command_type"], "knowledge_source")

    async def test_unmentioned_personal_fact_is_not_claimed(self):
        self.processor._last_memory_ids = [self.fact["id"]]
        self.processor._cache_reply("Гарібальді народився в Ніцці.")
        self.assertIsNone(self.processor._knowledge_source_reply("Звідки ти це знаєш?"))

    async def test_turn_records_which_facts_reached_the_model(self):
        self.llm.converse = AsyncMock(return_value=NaturalTurn("chat", "Кафедра в корпусі 7."))
        text = "Де кафедра кібербезпеки?"
        await self.processor.process(TurnEnvelope(turn_id="m", session_id="m", source="text", text=text,
                                                  transcript=text, stt_engine="text", confidence=1.0), AsyncMock())
        self.assertEqual(self.processor._last_memory_ids, [self.fact["id"]])

    def test_fact_without_topic_is_shown_once(self):
        from skills.memory.personal import fact_text
        self.assertEqual(fact_text("кафедра у корпусі П", "кафедра у корпусі П"), "кафедра у корпусі П")
        self.assertEqual(fact_text("браузер", "Firefox"), "браузер: Firefox")

    def test_database_spelling_and_gap_messages(self):
        from core.action_policy import KNOWLEDGE_GAP_NOTICE, _gap_refusal
        self.assertEqual(parse_knowledge_request("Що є в базі данних?"), ("list", "", ""))
        search = CommandIntent("web_search", {"query": "кафедра"})
        self.assertEqual(_gap_refusal("Ти помиляєшся, вона там є.", search), KNOWLEDGE_GAP_NOTICE)
        self.assertIn("не запускаю", _gap_refusal("Не шукай в інтернеті, хто такий Гарібальді?", search))
        self.assertIn("чутливі дані", _gap_refusal("Який мій пароль 1234?", search))
        self.assertIsNone(_gap_refusal("Хто такий Гарібальді?", search))

    def test_notepad_alias_is_a_default(self):
        from config import default_config
        aliases = default_config()["application_aliases"]
        self.assertEqual((aliases["блокнот"], aliases["пейнт"]), ("Notepad", "Paint"))


class LiveTestFixesRound5(unittest.IsolatedAsyncioTestCase):
    """Live test 06.11.2026 (E274) after phase 7."""

    async def _slow_voice_turn(self, groq):
        processor, _router, llm, _memory, speaker = build("chat", True)
        processor.settings.llm_filler_seconds = 0.05

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.4)
            return NaturalTurn("chat", "Відповідь.")
        llm.converse = slow
        text = "Розкажи щось цікаве про космос"
        envelope = TurnEnvelope(turn_id="f", session_id="f", source="voice", text=text, transcript=text,
                                stt_engine="groq" if groq else "vosk", confidence=0 if groq else .95,
                                confidence_available=not groq, action_eligible=not groq)
        await processor.process(envelope, AsyncMock())
        return [call.args for call in speaker.say.await_args_list]

    async def test_filler_speaks_on_the_unscored_groq_path_too(self):
        # The first filler missed it: all live Groq turns go through the unscored path (60 s of silence).
        for groq in (True, False):
            with self.subTest(groq=groq):
                spoken = await self._slow_voice_turn(groq)
                self.assertEqual(spoken.count(("Хвилинку.",)), 1)

    def test_stt_spelling_of_open_is_repaired(self):
        from core.natural_turn import direct_request, final_request, voice_request
        for text in ("Відкрай блокнот.", "відкрай блокнот"):
            with self.subTest(text=text):
                self.assertTrue(direct_request(final_request(voice_request(text)), "open_app"), text)
        self.assertFalse(direct_request(final_request(voice_request("Я вчора відкрив браузер")), "open_app"))

    def test_bare_challenge_does_not_start_a_search(self):
        from core.action_policy import KNOWLEDGE_GAP_NOTICE, _gap_refusal
        search = CommandIntent("web_search", {"query": "кафедра кібербезпеки Луцький технічний"})
        for text in ("Точно?", "Серйозно?", "Невже?"):
            with self.subTest(text=text):
                self.assertEqual(_gap_refusal(text, search), KNOWLEDGE_GAP_NOTICE)
        self.assertIsNone(_gap_refusal("Хто такий Гарібальді?", search))
        self.assertIsNone(_gap_refusal("Який курс долара?", search))

    def test_forget_phrases_with_personal_memory_wording(self):
        from skills.memory.personal import forget_query, matching_facts
        for text, query in (("інформацію з особистої пам'яті про кафедру кібербезпеки", "кафедру кібербезпеки"),
                            ("про проєкт", "проєкт"), ("інформацію з особистої пам'яті", ""), ("про", "")):
            with self.subTest(text=text):
                self.assertEqual(forget_query(text), query)
        facts = [{"key": "кафедра кібербезпеки", "value": "корпус 7"}, {"key": "браузер", "value": "Firefox"}]
        self.assertEqual(matching_facts(facts, "кафедру кібербезпеки"), facts[:1])
        self.assertEqual(matching_facts(facts, "про"), [])   # stop words only: never everything
        self.assertEqual(matching_facts(facts, ""), [])

    async def test_forget_without_a_topic_changes_nothing(self):
        from skills.memory.personal import handle_request
        from services.storage.memory_store import MemoryStore
        with tempfile.TemporaryDirectory() as temp:
            memory = MemoryStore(Path(temp) / "memory.json")
            memory.remember("кафедра", "корпус 7", category="user_explicit")
            confirm = AsyncMock(return_value=True)
            context = SimpleNamespace(services={"memory": memory}, confirm=confirm)
            result = await handle_request(("forget", "інформацію з особистої пам'яті"), context, Mock())
            self.assertIn("Назвіть, що саме забути", result.response)
            confirm.assert_not_awaited()
            self.assertEqual(len(memory.all()), 1)
            result = await handle_request(("forget", "інформацію з особистої пам'яті про кафедру"), context, Mock())
            confirm.assert_awaited_once()
            self.assertEqual(memory.all(), [])


class SpokenClaimTests(unittest.TestCase):
    """Live test 06.11.2026 (E269): claims spoken although nothing changed."""

    def test_volume_and_settings_claims_are_screened(self):
        from core.processor import _claims_local_change
        for text in ("Зменшую гучність.", "Збільшив гучність на 3.", "Вмикаю звук.", "Перемикаю на навушники.",
                     "Налаштував мікрофон."):
            with self.subTest(text=text):
                self.assertTrue(_claims_local_change(text))
        for text in ("Гучність можна зменшити командою.", "Я можу зменшити гучність, якщо скажете «Команда».",
                     "Перепрошую."):
            with self.subTest(text=text):
                self.assertFalse(_claims_local_change(text))

    def test_bare_protocol_token_is_never_spoken(self):
        from core.processor import _claims_local_change
        for text in ("UNSUPPORTED", "unsupported.", "CHAT", "Lookup"):
            with self.subTest(text=text):
                self.assertTrue(_claims_local_change(text))
        self.assertFalse(_claims_local_change("Ця функція поки unsupported у бета-версії, на жаль."))

    def test_forgotten_name_claim_is_replaced(self):
        self.assertTrue(memory_change_claim("Зрозумів, забув це ім'я."))
        self.assertTrue(memory_change_claim("Запам’ятав ваше ім’я."))
        self.assertFalse(memory_change_claim("Я забув, як це називається."))
        self.assertFalse(memory_change_claim("Ваше ім'я Вурадзі."))


class GroqSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Live test 06.11.2026: «Так.» to «Відкрити цей файл?» was refused for unscored Groq voice."""

    def setUp(self):
        from core.task_context import TaskContext
        self.processor, _router, self.llm, _memory, _speaker = build("chat", True)
        self.tasks = TaskContext()
        self.processor.services["tasks"] = self.tasks
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dyplom.txt"
        self.path.write_text("fixture", encoding="utf-8")
        patcher = patch.object(self.tasks, "open_file", new_callable=AsyncMock,
                               return_value=SkillResult(True, "opened", {"command_type": "file_open"}))
        self.open_file = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def envelope(text, **flags):
        return TurnEnvelope(turn_id=text, session_id="sel", source="voice", text=text, transcript=text,
                            stt_engine="groq", confidence=0, confidence_available=False,
                            action_eligible=False, **flags)

    async def test_clean_groq_yes_opens_the_single_offered_file_once(self):
        self.tasks.offer("file", [self.path], command_type="file_search")
        result = await self.processor.process(self.envelope("Так."), AsyncMock())
        self.assertEqual(result.response, "opened")
        self.open_file.assert_awaited_once()
        self.assertEqual(self.open_file.await_args.args[0], self.path)
        self.assertIsNone(self.tasks.pending)  # consumed once, never replayed

    async def test_groq_no_cancels_and_damaged_reply_never_selects(self):
        self.tasks.offer("file", [self.path], command_type="file_search")
        result = await self.processor.process(self.envelope("Ні."), AsyncMock())
        self.assertEqual(result.data["command_type"], "task_cancelled")
        for flags in ({"recognition_unreliable": True}, {"capture_truncated": True},
                      {"utterance_incomplete": True}):
            with self.subTest(flags=flags):
                self.tasks.offer("file", [self.path], command_type="file_search")
                await self.processor.process(self.envelope("Так.", **flags), AsyncMock())
                self.open_file.assert_not_awaited()

    async def test_without_an_offer_yes_is_not_a_selection(self):
        await self.processor.process(self.envelope("Так."), AsyncMock())
        self.open_file.assert_not_awaited()

    async def test_voice_loop_gate_passes_clean_groq_selection(self):
        from core.app import RavenApp
        self.tasks.offer("file", [self.path], command_type="file_search")
        app = object.__new__(RavenApp)
        app.command_queue, app.command_idle = asyncio.Queue(), asyncio.Event()
        app.running, app.web_ui = True, None
        app.services, app.processor = self.processor.services, self.processor
        app.speaker = SimpleNamespace(say=AsyncMock(), stop=AsyncMock())
        app.settings = SimpleNamespace(stt_command_confidence_threshold=.8, stt_chat_confidence_threshold=.6,
                                       natural_actions_enabled=True)
        app.confirmation = SimpleNamespace(ask=AsyncMock(return_value=False))
        recognition = RecognitionResult("Так.", 0.0, engine="groq", fragmented=True, utterance_incomplete=False,
                                        confidence_available=False)
        await app._enqueue_command("Так.", "voice", 0.0, recognition=recognition)
        task = asyncio.create_task(app._command_loop())
        with self.assertLogs("core.app", level="INFO") as logs:
            try:
                await asyncio.wait_for(app.command_queue.join(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.open_file.assert_awaited_once()
        # Roadmap phase 0: one content-free route line per turn.
        routes = [line for line in logs.output if "turn.route" in line]
        self.assertEqual(len(routes), 1)
        self.assertIn("source=voice engine=groq scored=False", routes[0])
        self.assertNotIn("Так", routes[0])
        self.assertNotIn("Не розібрав фразу. Повторіть її, будь ласка.",
                         [call.args[0] for call in app.speaker.say.await_args_list])


if __name__ == "__main__":
    unittest.main()
