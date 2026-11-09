"""Raven has no secrets vault: password commands are refused, never stored or read."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from skills.memory import skill


def run(command, raw, source='text'):
    memory = Mock()
    context = SimpleNamespace(raw_text=raw, source=source, confirm=Mock(side_effect=AssertionError('no confirmation')))
    result = asyncio.run(skill.handle(command, context, {'memory': memory}))
    return result, memory


class NoPasswordTests(unittest.TestCase):
    def test_storing_a_password_is_refused_and_memory_untouched(self):
        for source in ('text', 'voice'):
            result, memory = run("запам'ятай пароль від github це abc", "запам'ятай пароль від GitHub це AbC", source)
            self.assertTrue(result.handled)
            self.assertEqual(result.data['command_type'], 'secret_unsupported')
            self.assertIn('не зберігає', result.response)
            memory.remember.assert_not_called()

    def test_reading_a_password_is_refused(self):
        result, memory = run('пароль від github', 'пароль від GitHub')
        self.assertEqual(result.data['command_type'], 'secret_unsupported')
        memory.remember.assert_not_called()

    def test_ordinary_memory_still_works(self):
        result, memory = run("запам'ятай що мене звати Тарас це так", "запам'ятай, що мене звати Тарас це так")
        self.assertEqual(result.data['command_type'], 'memory_write')
        memory.remember.assert_called_once()


if __name__ == '__main__':
    unittest.main()
