"""Phase 8: honest confidence label for cloud STT and a journal line for button confirmations."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.app import RavenApp, _confidence_label


class ConfidenceLabelTests(unittest.TestCase):
    def test_cloud_result_without_confidence_is_not_zero(self):
        groq = SimpleNamespace(confidence=0.0, confidence_available=False)
        self.assertEqual(_confidence_label(groq), 'n/a')
        self.assertEqual(_confidence_label(groq, 'unscored'), 'unscored')

    def test_available_confidence_is_shown(self):
        vosk = SimpleNamespace(confidence=0.876, confidence_available=True)
        self.assertEqual(_confidence_label(vosk), '0.88')


class ButtonConfirmationLogTests(unittest.TestCase):
    def app(self, awaiting=True):
        confirmation = SimpleNamespace(awaiting=awaiting, request_id='abcdef1234567890', submit=Mock())
        return SimpleNamespace(confirmation=confirmation, web_ui=Mock())

    def control(self, app, data):
        return asyncio.run(RavenApp.web_control(app, 'confirm', data))

    def test_accept_and_deny_are_logged_without_dialog_text(self):
        for accept, outcome in ((True, 'confirm'), (False, 'deny')):
            app = self.app()
            with self.assertLogs('core.app', 'INFO') as logs:
                status, _ = self.control(app, {'request_id': 'abcdef1234567890', 'accept': accept})
            self.assertEqual(status, 200)
            line = ' '.join(logs.output)
            self.assertIn(f'confirmation_button outcome={outcome} source=ui request=abcdef12', line)
            self.assertNotIn('abcdef1234567890', line)
            app.confirmation.submit.assert_called_once_with('так' if accept else 'ні', 'abcdef1234567890')

    def test_stale_button_is_logged_and_does_not_submit(self):
        app = self.app()
        with self.assertLogs('core.app', 'INFO') as logs:
            status, _ = self.control(app, {'request_id': 'other', 'accept': True})
        self.assertEqual(status, 409)
        self.assertIn('confirmation_button outcome=rejected', ' '.join(logs.output))
        app.confirmation.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
