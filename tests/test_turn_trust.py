"""Roadmap phase 1: TrustLevel labels and the single reply screen."""
import unittest
from dataclasses import replace

from core.models import TurnEnvelope
from core.reply_screen import CHAT_MODE_NOTICE, NATURAL_NOTICE, claims_local_change, screen
from core.turn_gate import TrustLevel, TurnGate
from tests.processor_gate_matrix import TRUST


def gate(trust, **flags):
    base = TurnEnvelope(turn_id="t", session_id="t", source="voice", text="x", transcript="x",
                        stt_engine="vosk", confidence=0.95)
    return TurnGate.evaluate(replace(base, **TRUST[trust], **flags), True, False)


class TrustLevelTests(unittest.TestCase):
    def test_every_matrix_trust_maps_to_one_level(self):
        expected = {"reliable": TrustLevel.SCORED, "not_eligible": TrustLevel.SCORED,
                    "unreliable": TrustLevel.DAMAGED, "truncated": TrustLevel.DAMAGED,
                    "incomplete": TrustLevel.DAMAGED, "unscored_groq": TrustLevel.UNSCORED_CLEAN}
        for trust, level in expected.items():
            with self.subTest(trust=trust):
                self.assertIs(gate(trust).trust, level)

    def test_unscored_groq_is_never_scored_or_eligible(self):
        unscored = gate("unscored_groq")
        self.assertFalse(unscored.allow_actions)
        self.assertTrue(unscored.may_select_offer)
        damaged = gate("unscored_groq", capture_truncated=True)
        self.assertIs(damaged.trust, TrustLevel.DAMAGED)
        self.assertFalse(damaged.may_select_offer)
        self.assertFalse(damaged.groq_candidate(True))

    def test_text_callers_and_caller_veto(self):
        self.assertIs(TurnGate.evaluate(None, True, False).trust, TrustLevel.SCORED)
        self.assertIs(TurnGate.evaluate(None, True, True).trust, TrustLevel.DAMAGED)
        base = TurnEnvelope(turn_id="t", session_id="t", source="voice", text="x", transcript="x",
                            stt_engine="vosk", confidence=0.95)
        vetoed = TurnGate.evaluate(replace(base, **TRUST["unscored_groq"]), False, False)
        self.assertFalse(vetoed.may_select_offer)
        self.assertFalse(vetoed.groq_candidate(True))

    def test_lexical_fragment_only_for_unscored_cut_off_text(self):
        self.assertTrue(gate("unscored_groq", utterance_incomplete=True).lexical_fragment)
        self.assertFalse(gate("incomplete").lexical_fragment)  # scored: a repeat is asked
        self.assertFalse(gate("unscored_groq", utterance_incomplete=True, capture_truncated=True).lexical_fragment)


class ReplyScreenTests(unittest.TestCase):
    def test_claims_and_tokens_are_replaced_with_the_mode_notice(self):
        for text in ("Зменшую гучність.", "**Відкриваю** блокнот.", "UNSUPPORTED", "lookup.",
                     "Запам'ятав ваше ім'я."):
            with self.subTest(text=text):
                self.assertTrue(claims_local_change(text.strip("*")))
                self.assertEqual(screen(text), NATURAL_NOTICE)
                self.assertEqual(screen(text, CHAT_MODE_NOTICE), CHAT_MODE_NOTICE)

    def test_ordinary_prose_is_kept_without_markdown(self):
        self.assertEqual(screen("**Марс — четверта планета.**"), "Марс — четверта планета.")
        self.assertEqual(screen("Гучність можна зменшити в налаштуваннях."),
                         "Гучність можна зменшити в налаштуваннях.")


if __name__ == "__main__":
    unittest.main()
