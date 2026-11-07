"""Roadmap phase 4: first-text timeout with a backup model, status for long actions.

Live test E274: Gemini stayed silent for 60 s and the natural turn had no backup at all.
The silence wait (endpoint) is deliberately unchanged (user decision 06.11.2026).
"""
import asyncio
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import config
from core import command_actions
from core.command_intent import CommandIntent
from core.models import CommandContext, SkillResult
from services.llm.first_text import FirstTextTimeout, await_first_text, first_text_seconds
from services.llm.manager import LLMManager


def manager(providers, *, first=0.05, total=2):
    llm = object.__new__(LLMManager)
    llm.settings = SimpleNamespace(history_limit=12, llm_total_timeout_seconds=total,
                                   llm_failures_before_switch=3, llm_first_text_timeout_seconds=first)
    llm.history = Mock()
    llm.history.load.return_value = {'messages': []}
    llm.system_prompt = 'Fixture'
    llm._append_history = Mock()
    llm.providers = providers
    llm.available_order = list(providers)
    llm.active_name = llm.available_order[0]
    llm._cooldowns = {}
    return llm


class Silent:
    """A provider that never sends text; records whether its stream was closed."""

    def __init__(self):
        self.calls = 0
        self.closed = False

    async def chat_stream(self, messages):
        self.calls += 1
        try:
            await asyncio.sleep(30)
            yield 'CHAT\nЗапізніла відповідь.'
        finally:
            self.closed = True


def answering(text, *, delay=0.0, calls=None):
    async def stream(messages):
        if calls is not None:
            calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        yield text
    return stream


class FirstTextHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_text_raises_and_cancels_the_request(self):
        cancelled = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        began = time.perf_counter()
        with self.assertRaises(FirstTextTimeout):
            await await_first_text(work(), asyncio.Event(), 0.05, 2)
        self.assertLess(time.perf_counter() - began, 1)
        self.assertTrue(cancelled.is_set())

    async def test_started_request_keeps_the_total_budget(self):
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(0.15)
            return 'готово'

        self.assertEqual(await await_first_text(work(), started, 0.05, 2), 'готово')

    async def test_disabled_or_last_provider_waits_normally(self):
        async def work():
            await asyncio.sleep(0.12)
            return 'готово'

        self.assertEqual(await await_first_text(work(), asyncio.Event(), 0, 2), 'готово')

    async def test_total_budget_still_applies(self):
        async def work():
            await asyncio.sleep(30)

        with self.assertRaises(TimeoutError) as raised:
            await await_first_text(work(), asyncio.Event(), 0, 0.05)
        self.assertNotIsInstance(raised.exception, FirstTextTimeout)

    def test_setting_reader_rejects_non_numbers(self):
        for value, expected in ((5, 5.0), (0, 0.0), (-1, 0.0), (True, 0.0), ('5', 0.0), (None, 0.0)):
            self.assertEqual(first_text_seconds(SimpleNamespace(llm_first_text_timeout_seconds=value)), expected)
        self.assertEqual(first_text_seconds(SimpleNamespace()), 0.0)


class NaturalTurnBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_primary_switches_to_backup_once(self):
        primary, spoken = Silent(), []
        llm = manager({'gemini': primary, 'groq': SimpleNamespace(chat_stream=answering('CHAT\nВідповідь резерву.'))})

        async def emit(text):
            spoken.append(text)

        began = time.perf_counter()
        with patch('builtins.print'), self.assertLogs('services.llm.manager', level='WARNING') as logs:
            result = await llm.converse('Привіт', on_chunk=emit)
        self.assertLess(time.perf_counter() - began, 1)
        self.assertEqual((result.kind, result.response), ('chat', 'Відповідь резерву.'))
        self.assertEqual(spoken, ['Відповідь резерву.'])
        self.assertTrue(primary.closed)
        self.assertEqual(primary.calls, 1)
        self.assertEqual(llm.active_name, 'gemini')  # next turn tries the primary first again
        self.assertIn('reason=first_text_timeout', '\n'.join(logs.output))
        llm._append_history.assert_called_once_with('Привіт', 'Відповідь резерву.')

    async def test_started_primary_is_never_replayed_on_backup(self):
        backup_calls = []

        async def slow_after_first(messages):
            yield 'CHAT\nПерше речення. '
            await asyncio.sleep(0.15)
            yield 'Друге речення.'

        llm = manager({'gemini': SimpleNamespace(chat_stream=slow_after_first),
                       'groq': SimpleNamespace(chat_stream=answering('CHAT\nНе має звучати.', calls=backup_calls))})
        result = await llm.converse('Привіт', on_chunk=AsyncMock())
        self.assertEqual(result.response, 'Перше речення. Друге речення.')
        self.assertEqual(backup_calls, [])

    async def test_invalid_or_blocked_output_never_goes_to_backup(self):
        backup_calls = []
        llm = manager({'gemini': SimpleNamespace(chat_stream=answering('ACTION\n{не json')),
                       'groq': SimpleNamespace(chat_stream=answering('ACTION\n{"tool":"open_app","arguments":'
                                                                     '{"name":"Notepad"}}', calls=backup_calls))})
        with self.assertLogs('services.llm.manager', level='WARNING'):
            result = await llm.converse('Відкрий блокнот', enabled_skills={'apps'})
        self.assertEqual(result.kind, 'unavailable')
        self.assertIn('Нічого не виконано', result.response)
        self.assertEqual(backup_calls, [])

    async def test_connection_error_before_text_uses_backup(self):
        async def offline(messages):
            raise ConnectionError('offline')
            yield ''  # pragma: no cover

        llm = manager({'gemini': SimpleNamespace(chat_stream=offline),
                       'groq': SimpleNamespace(chat_stream=answering('CHAT\nЯ тут.'))})
        with patch('builtins.print'), self.assertLogs('services.llm.manager', level='WARNING'):
            result = await llm.converse('Привіт')
        self.assertEqual(result.response, 'Я тут.')

    async def test_without_backup_or_with_zero_setting_waits_for_slow_primary(self):
        for providers, first in (({'gemini': None}, 0.05), ({'gemini': None, 'groq': None}, 0)):
            backup_calls = []
            providers = {name: SimpleNamespace(chat_stream=answering('CHAT\nПовільно, але є.', delay=0.15)
                                               if name == 'gemini' else answering('CHAT\nНі.', calls=backup_calls))
                         for name in providers}
            result = await manager(providers, first=first).converse('Привіт')
            self.assertEqual(result.response, 'Повільно, але є.')
            self.assertEqual(backup_calls, [])

    async def test_all_providers_silent_ends_with_nothing_executed(self):
        llm = manager({'gemini': Silent(), 'groq': Silent()}, total=0.3)
        with patch('builtins.print'), self.assertLogs('services.llm.manager', level='WARNING'):
            result = await llm.converse('Привіт')
        self.assertEqual(result.kind, 'unavailable')
        self.assertIn('Нічого не виконано', result.response)


class ChatBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_chat_switches_without_retrying_the_slow_model(self):
        primary = Silent()
        llm = manager({'gemini': primary, 'groq': SimpleNamespace(chat_stream=answering('Резерв відповів.'))})
        with patch('builtins.print'), self.assertLogs('services.llm.manager', level='ERROR'):
            answer = await llm.chat('Привіт', on_chunk=AsyncMock())
        self.assertEqual(answer, 'Резерв відповів.')
        self.assertEqual(primary.calls, 1)
        self.assertTrue(primary.closed)
        self.assertEqual(llm.active_name, 'gemini')

    async def test_non_streamed_chat_is_not_cut_by_first_text_timeout(self):
        async def slow(messages):
            await asyncio.sleep(0.15)
            return 'Повна відповідь.'

        backup = AsyncMock(return_value='Ні.')
        llm = manager({'gemini': SimpleNamespace(chat=slow), 'groq': SimpleNamespace(chat=backup)})
        self.assertEqual(await llm.chat('Привіт'), 'Повна відповідь.')
        backup.assert_not_awaited()


class FirstTextConfigTests(unittest.TestCase):
    def test_default_and_validation(self):
        defaults = config.default_config()
        self.assertEqual(defaults['llm']['first_text_timeout_seconds'], 5.0)
        config.validate_config(copy.deepcopy(defaults))
        for bad in (-1, 61, True, '5'):
            broken = copy.deepcopy(defaults)
            broken['llm']['first_text_timeout_seconds'] = bad
            with self.assertRaises(config.ConfigError):
                config.validate_config(broken)


class LongActionStatusTests(unittest.IsolatedAsyncioTestCase):
    def context(self, source='voice', confirm=None):
        speaker = SimpleNamespace(say=AsyncMock())
        services = {'speaker': speaker, 'enabled_skills': {'files', 'web'}}
        return CommandContext(SimpleNamespace(), services, confirm or AsyncMock(return_value=True), source), speaker

    async def test_slow_work_gets_one_status_and_its_own_result(self):
        context, speaker = self.context()

        async def slow():
            await asyncio.sleep(0.15)
            return 'знайдено'

        self.assertEqual(await command_actions.with_status(slow(), context, 0.05), 'знайдено')
        speaker.say.assert_awaited_once_with('Ще шукаю.')

    async def test_fast_work_or_text_source_stays_silent(self):
        for source, delay in (('voice', 0.0), ('voice', 1.0), ('text', 0.01)):
            context, speaker = self.context(source)

            async def work():
                await asyncio.sleep(0.03)
                return 'ок'

            self.assertEqual(await command_actions.with_status(work(), context, delay), 'ок')
            speaker.say.assert_not_awaited()

    async def test_find_files_status_does_not_count_the_confirmation_wait(self):
        async def slow_confirm(prompt):
            await asyncio.sleep(0.15)
            return True

        async def fast_search(query, extension, context):
            return SkillResult(True, 'Знайдено 1 файл.')

        context, speaker = self.context(confirm=slow_confirm)
        intent = CommandIntent('find_files', {'query': 'звіт', 'extension': ''})
        with patch.object(command_actions, 'LONG_ACTION_STATUS_SECONDS', 0.05), \
                patch('skills.files.skill.find_files', fast_search):
            result = await command_actions.execute_intent(intent, context)
        self.assertEqual(result.response, 'Знайдено 1 файл.')
        speaker.say.assert_not_awaited()

    async def test_slow_web_search_says_status_once(self):
        async def slow_search(query, services):
            await asyncio.sleep(0.15)
            return SkillResult(True, 'Результат пошуку.')

        context, speaker = self.context()
        intent = CommandIntent('web_search', {'query': 'погода у Луцьку'})
        with patch.object(command_actions, 'LONG_ACTION_STATUS_SECONDS', 0.05), \
                patch('skills.web.skill.search_web', slow_search):
            result = await command_actions.execute_intent(intent, context)
        self.assertEqual(result.response, 'Результат пошуку.')
        speaker.say.assert_awaited_once_with('Ще шукаю.')


if __name__ == '__main__':
    unittest.main()
