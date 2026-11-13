"""Журнал дій сесії та підказки дозволів macOS."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.models import SkillResult
from core.processor import CommandProcessor
from services.platform import PlatformOperationError
from services.platform import macos
from services.platform.resolver import permission_reply


def processor():
    item = object.__new__(CommandProcessor)
    item.llm_manager = SimpleNamespace(record_local_action=Mock())
    return item


class ActionJournalTests(unittest.IsolatedAsyncioTestCase):
    async def record(self, item, data, response='Готово.', answer=None, command='Заблокуй екран'):
        confirm = item._watch_confirm(AsyncMock(return_value=answer))
        if answer is not None:
            self.assertIs(await confirm('Заблокувати екран?'), answer)
        item._remember_local_action(SkillResult(True, response, {'natural_action': True, **data}),
                                    'local', command, None, {})
        return item.action_journal[-1]

    async def test_status_and_confirmation_are_recorded_per_turn(self):
        item = processor()
        for data, answer, status, confirmation in (
                ({'command_type': 'lock', 'accepted': True, 'verified': True}, True, 'verified', 'accepted'),
                ({'command_type': 'open_application', 'accepted': True, 'verified': False}, None, 'submitted', 'none'),
                ({'command_type': 'screenshot', 'accepted': False, 'success': False}, True, 'failed', 'accepted'),
                ({'command_type': 'interpretation_cancelled'}, False, 'cancelled', 'declined')):
            with self.subTest(status=status):
                entry = await self.record(item, data, answer=answer)
                self.assertEqual((entry['status'], entry['confirmation']), (status, confirmation))
                self.assertRegex(entry['time'], r'^\d\d:\d\d$')

    async def test_confirmation_does_not_leak_into_the_next_turn(self):
        item = processor()
        await self.record(item, {'command_type': 'lock', 'verified': True}, answer=True)
        entry = await self.record(item, {'command_type': 'media', 'accepted': True})
        self.assertEqual(entry['confirmation'], 'none')

    async def test_journal_is_bounded_redacted_and_skips_plain_chat(self):
        item = processor()
        for index in range(CommandProcessor.JOURNAL_LIMIT + 5):
            await self.record(item, {'command_type': 'media', 'accepted': True}, command=f'команда {index}')
        self.assertEqual(len(item.action_journal), CommandProcessor.JOURNAL_LIMIT)
        self.assertEqual(item.action_journal[-1]['command'], f'команда {CommandProcessor.JOURNAL_LIMIT + 4}')
        entry = await self.record(item, {'command_type': 'media'},
                                  command='запам\'ятай API_KEY=' + 'a' * 40 + ' ' + 'x' * 300)
        self.assertNotIn('a' * 40, entry['command'])
        self.assertIn('[REDACTED]', entry['command'])
        self.assertLessEqual(len(entry['command']), 120)
        before = len(item.action_journal)
        item._remember_local_action(SkillResult(True, 'Привіт.', {'command_type': 'chat'}), 'gemini', 'Привіт', None, {})
        self.assertEqual(len(item.action_journal), before)


class PermissionHintTests(unittest.TestCase):
    def test_macos_failures_name_the_missing_permission(self):
        for reason, hint in (('screen_recording_denied', 'screen_recording'),
                             ('macos_privacy_permission', 'accessibility'), ('os_permission', '')):
            with self.subTest(reason=reason), self.assertLogs('services.platform.macos', 'WARNING'):
                with self.assertRaises(PlatformOperationError) as caught:
                    macos._fail('screenshot', 'permission_denied', reason)
                self.assertEqual(caught.exception.hint, hint)

    def test_reply_adds_fixed_guidance_only_for_known_hints(self):
        text, extra = permission_reply(PlatformOperationError('screenshot', 'permission_denied', 'screen_recording'),
                                       'Знімок екрана не збережено.')
        self.assertIn('Запис екрана й системного аудіо', text)
        self.assertEqual(extra, {'permission_hint': 'screen_recording'})
        text, extra = permission_reply(PlatformOperationError('lock', 'execution_failed'), 'Екран не заблоковано.')
        self.assertEqual(text, 'Не вдалося виконати дію. Екран не заблоковано.')
        self.assertEqual(extra, {})
        text, extra = permission_reply(PlatformOperationError('lock', 'permission_denied', 'http://evil'), 'x')
        self.assertEqual(extra, {})


if __name__ == '__main__':
    unittest.main()
