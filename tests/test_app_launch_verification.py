"""Перевірка запуску програми: результат у даних (журнал дій), відповідь однакова й не озвучує перевірку."""
import asyncio
import platform
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from skills.apps.skill import launch_result


class LaunchResultTests(unittest.TestCase):
    def run_launch(self, evidence):
        apps = SimpleNamespace(verify_launch=lambda app: evidence)
        return asyncio.run(launch_result(apps, True, 'Safari', {'name': 'Safari', 'command': '/Applications/Safari.app'}))

    def test_verified_window_is_recorded_but_the_reply_stays_the_same(self):
        verified = self.run_launch({'process': True, 'window': True})
        plain = self.run_launch({'process': True, 'window': False})
        self.assertEqual(verified.response, plain.response)
        self.assertEqual(verified.response, 'Команду запуску Safari передано системі.')
        self.assertTrue(verified.data['verified'])
        self.assertEqual(verified.data['status'], 'verified')
        self.assertFalse(plain.data['verified'])
        self.assertEqual(plain.data['status'], 'submitted')


@unittest.skipUnless(platform.system() == 'Darwin', 'macOS only')
class MacEvidenceTests(unittest.TestCase):
    def test_unknown_application_has_no_process(self):
        from services.platform.macos import application_evidence
        self.assertEqual(application_evidence({'name': 'Немає-такої-програми', 'command': '/Applications/NoSuch.app'}),
                         {'process': False, 'window': False})

    def test_controller_polls_until_a_window_appears(self):
        from services.apps.controller import ApplicationController
        answers = iter([{'process': True, 'window': False}, {'process': True, 'window': True}])
        with patch('services.platform.macos.application_evidence', side_effect=lambda app: next(answers)), \
                patch('services.apps.controller.time.sleep'):
            self.assertEqual(ApplicationController._verify_posix({'name': 'Safari', 'command': '/Applications/Safari.app'}),
                             {'process': True, 'window': True})


if __name__ == '__main__':
    unittest.main()
