import unittest
from unittest.mock import Mock

from services.llm.manager import LLMManager
from services.llm.providers import GroqProvider


class _Denied(Exception):
    status_code = 403


def _manager():
    manager = object.__new__(LLMManager)
    manager._cooldowns = {}
    return manager


class GroqHealthcheckTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, probe_error=None):
        provider = GroqProvider("openai/gpt-oss-20b", "key", 5)
        client = Mock()
        client.models.list.return_value = []
        if probe_error:
            client.chat.completions.create.side_effect = probe_error
        provider._client = lambda: client
        return provider, client

    async def test_valid_key_but_blocked_model_is_not_available(self):
        provider, _ = self.provider(_Denied("PRIVATE_BODY"))
        status = await provider.healthcheck()
        self.assertFalse(status.available)
        self.assertTrue(status.denied)

    async def test_accessible_model_is_available_and_probe_is_one_token(self):
        provider, client = self.provider()
        status = await provider.healthcheck()
        self.assertTrue(status.available)
        self.assertFalse(status.denied)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["max_tokens"], 1)

    async def test_probe_timeout_does_not_prove_denial(self):
        provider, _ = self.provider(TimeoutError())
        status = await provider.healthcheck()
        self.assertTrue(status.available)


class DeniedBackupTests(unittest.TestCase):
    def test_denied_provider_is_not_a_usable_backup(self):
        manager = _manager()
        self.assertEqual(manager._usable_backup(["groq", "ollama"]), ["groq", "ollama"])
        manager._mark_denied("groq", True)
        self.assertEqual(manager._usable_backup(["groq", "ollama"]), ["ollama"])
        manager._mark_denied("groq", False)
        self.assertEqual(manager._usable_backup(["groq"]), ["groq"])

    def test_access_denied_failure_marks_provider_denied(self):
        manager = _manager()
        manager._cool_down("groq", _Denied("x"))
        self.assertEqual(manager._usable_backup(["groq"]), [])

    def test_other_failures_do_not_mark_denied(self):
        manager = _manager()
        manager._cool_down("gemini", TimeoutError())
        self.assertEqual(manager._usable_backup(["gemini"]), ["gemini"])


if __name__ == "__main__":
    unittest.main()
