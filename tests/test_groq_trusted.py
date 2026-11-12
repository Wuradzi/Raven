"""stt.groq_trusted (рішення 12.11.2026): чистий текст Groq без confidence має право на дію.

Confidence не вигадується; пошкоджене мовлення права не отримує; інші рушії не зачеплені.
"""
import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace

from core.action_policy import PolicyOutcome
from core.app import RavenApp
from core.models import RecognitionResult, TurnEnvelope
from core.recognition_policy import RecognitionPolicy
from core.turn_gate import TrustLevel, TurnGate

BASE = TurnEnvelope(turn_id="t", session_id="s", source="voice", text="x", transcript="x",
                    stt_engine="groq", confidence=0.0, confidence_available=False,
                    action_eligible=True, cloud_trusted=True)


class TrustedGateTests(unittest.TestCase):
    def test_clean_trusted_groq_is_equal_to_scored_stt(self):
        gate = TurnGate.evaluate(BASE, True, False)
        self.assertTrue(gate.allow_actions)
        self.assertTrue(gate.proposal_quality)
        self.assertTrue(gate.confidence_known)
        self.assertFalse(gate.groq_unscored)
        self.assertIs(gate.trust, TrustLevel.SCORED)
        self.assertIs(gate.outcome(stop_phrase=False), PolicyOutcome.ALLOW)

    def test_damaged_trusted_groq_still_reaches_no_action(self):
        for field in ("recognition_unreliable", "capture_truncated", "utterance_incomplete", "clarification_required"):
            with self.subTest(field=field):
                gate = TurnGate.evaluate(replace(BASE, **{field: True}), True, False)
                self.assertFalse(gate.reliable and gate.proposal_quality)
                self.assertFalse(gate.proposal_quality)

    def test_caller_prohibition_still_wins(self):
        self.assertFalse(TurnGate.evaluate(BASE, False, False).allow_actions)

    def test_flag_is_ignored_without_cloud_trusted_or_for_other_engines(self):
        self.assertFalse(TurnGate.evaluate(replace(BASE, cloud_trusted=False), True, False).allow_actions)
        self.assertFalse(TurnGate.evaluate(replace(BASE, stt_engine="vosk"), True, False).allow_actions)
        self.assertIs(TurnGate.evaluate(replace(BASE, cloud_trusted=False, action_eligible=False), True, False).trust,
                      TrustLevel.UNSCORED_CLEAN)


class TrustedFlagsTests(unittest.TestCase):
    def flags(self, **values):
        # Реальний результат Groq: fragmented=True завжди (плейсхолдер відсутнього confidence).
        result = RecognitionResult(text="Відкрий браузер", confidence=0.0, engine="groq",
                                   confidence_available=False, fragmented=True, **values)
        return result, RecognitionPolicy.turn_flags(result)

    def test_clean_groq_gets_eligibility_without_a_confidence_score(self):
        result, flags = self.flags()
        self.assertFalse(flags["action_eligible"])  # без налаштування нічого не змінюється
        trusted = RecognitionPolicy.groq_trusted_flags(result, flags)
        self.assertTrue(trusted["action_eligible"])
        self.assertTrue(trusted["cloud_trusted"])
        self.assertFalse(trusted["confidence_available"])

    def test_damaged_fragmented_or_other_engine_stays_restricted(self):
        for values in ({"recognition_unreliable": True}, {"capture_truncated": True}, {"incomplete": True},
                       {"utterance_incomplete": True}):
            with self.subTest(values=values):
                result, flags = self.flags(**values)
                self.assertEqual(RecognitionPolicy.groq_trusted_flags(result, flags), flags)
        for result in (RecognitionResult("x", 0.9, "vosk"),
                       RecognitionResult("x", 0.9, "groq", confidence_available=True)):
            flags = RecognitionPolicy.turn_flags(result)
            self.assertNotIn("cloud_trusted", RecognitionPolicy.groq_trusted_flags(result, flags))


class TrustedIngressTests(unittest.IsolatedAsyncioTestCase):
    async def enqueue(self, trusted, **values):
        app = RavenApp.__new__(RavenApp)
        app.command_queue = asyncio.Queue()
        app.settings = SimpleNamespace(stt_groq_trusted=trusted)
        result = RecognitionResult(text="Відкрий браузер", confidence=0.0, engine="groq",
                                   confidence_available=False, fragmented=True, **values)
        return await app._enqueue_command(result.text, "voice", result.confidence, recognition=result)

    async def test_setting_controls_eligibility_at_ingress(self):
        on = await self.enqueue(True)
        self.assertTrue(on.action_eligible and on.cloud_trusted and not on.confidence_available)
        off = await self.enqueue(False)
        self.assertFalse(off.action_eligible or off.cloud_trusted)
        damaged = await self.enqueue(True, recognition_unreliable=True)
        self.assertFalse(damaged.action_eligible or damaged.cloud_trusted)


if __name__ == "__main__":
    unittest.main()
