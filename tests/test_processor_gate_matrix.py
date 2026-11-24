"""Characterization of CommandProcessor.process gating (audit 2026-10-29 #3, step 1).

Snapshot of the behaviour BEFORE refactoring, generated from the unchanged code
on 29.10.2026. A refactor must keep every row identical. A row may change only
as a deliberate, separately reviewed behaviour change with its own test.

Deliberate changes since the snapshot:
- 30.10.2026 (J125): "стоп" in pentest mode stops speech like in chat mode
  (10 rows; previously routed to the pentest skill).
- 24.11.2026: the pentest mode was removed from Raven; its rows were dropped.

Kept on purpose (J90): "стоп" from Groq without a confidence score gets no stop
exception and stays tool-free conversation; the UI stop button is unaffected.
"""
import unittest

from tests.processor_gate_matrix import FIELDS, cases, observe

EXPECTED = {
    ('chat', False, 'command', 'reliable'): ('routed', True, None, False, False, False, False, False, False),
    ('chat', False, 'command', 'not_eligible'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'command', 'unreliable'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'command', 'truncated'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'command', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'command', 'unscored_groq'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'reliable'): ('empty_command', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'not_eligible'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'unreliable'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'truncated'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'empty_command', 'unscored_groq'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'chat', 'reliable'): ('chat', False, None, True, False, False, False, False, False),
    ('chat', False, 'chat', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'chat', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'chat', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'chat', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'chat', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'natural_open', 'reliable'): ('chat', False, None, True, False, False, False, False, False),
    ('chat', False, 'natural_open', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'natural_open', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'natural_open', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'natural_open', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'natural_open', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'stop', 'reliable'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', False, 'stop', 'not_eligible'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', False, 'stop', 'unreliable'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', False, 'stop', 'truncated'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', False, 'stop', 'incomplete'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', False, 'stop', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'pause', 'reliable'): ('conversation_paused', False, None, False, False, False, False, False, False),
    ('chat', False, 'pause', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'pause', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'pause', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'pause', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    # 07.11.2026 (user decision, J149): a clean Groq «пауза розмови» pauses like a scored one, no model call.
    ('chat', False, 'pause', 'unscored_groq'): ('conversation_paused', False, None, False, False, False, False, False, False),
    ('chat', False, 'document', 'reliable'): ('', False, None, False, False, True, False, False, False),
    ('chat', False, 'document', 'not_eligible'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'document', 'unreliable'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'document', 'truncated'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'document', 'incomplete'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'document', 'unscored_groq'): ('', False, None, False, False, True, False, False, False),
    ('chat', False, 'memory', 'reliable'): ('memory_cancelled', False, None, False, False, False, True, False, False),
    ('chat', False, 'memory', 'not_eligible'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'memory', 'unreliable'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'memory', 'truncated'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'memory', 'incomplete'): ('', False, None, False, False, False, False, False, False),
    ('chat', False, 'memory', 'unscored_groq'): ('memory_cancelled', False, None, False, False, False, True, False, False),
    ('chat', False, 'ip', 'reliable'): ('chat', False, None, True, False, False, False, False, False),
    ('chat', False, 'ip', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'ip', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'ip', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', False, 'ip', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', False, 'ip', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'command', 'reliable'): ('routed', True, None, False, False, False, False, False, False),
    ('chat', True, 'command', 'not_eligible'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'command', 'unreliable'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'command', 'truncated'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'command', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'command', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'empty_command', 'reliable'): ('empty_command', False, None, False, False, False, False, False, False),
    ('chat', True, 'empty_command', 'not_eligible'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'empty_command', 'unreliable'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'empty_command', 'truncated'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'empty_command', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'empty_command', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'chat', 'reliable'): ('chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'chat', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'chat', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'chat', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'chat', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'chat', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'natural_open', 'reliable'): ('chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'natural_open', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'natural_open', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'natural_open', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'natural_open', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'natural_open', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'stop', 'reliable'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', True, 'stop', 'not_eligible'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', True, 'stop', 'unreliable'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', True, 'stop', 'truncated'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', True, 'stop', 'incomplete'): ('speech_stopped', False, None, False, False, False, False, False, True),
    ('chat', True, 'stop', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'pause', 'reliable'): ('conversation_paused', False, None, False, False, False, False, False, False),
    ('chat', True, 'pause', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'pause', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'pause', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'pause', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    # 07.11.2026 (user decision, J149): a clean Groq «пауза розмови» pauses like a scored one, no model call.
    ('chat', True, 'pause', 'unscored_groq'): ('conversation_paused', False, None, False, False, False, False, False, False),
    ('chat', True, 'document', 'reliable'): ('', False, None, False, False, True, False, False, False),
    ('chat', True, 'document', 'not_eligible'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'document', 'unreliable'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'document', 'truncated'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'document', 'incomplete'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'document', 'unscored_groq'): ('', False, None, False, False, True, False, False, False),
    ('chat', True, 'memory', 'reliable'): ('memory_cancelled', False, None, False, False, False, True, False, False),
    ('chat', True, 'memory', 'not_eligible'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'memory', 'unreliable'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'memory', 'truncated'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'memory', 'incomplete'): ('', False, None, False, False, False, False, False, False),
    ('chat', True, 'memory', 'unscored_groq'): ('memory_cancelled', False, None, False, False, False, True, False, False),
    ('chat', True, 'ip', 'reliable'): ('chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'ip', 'not_eligible'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'ip', 'unreliable'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'ip', 'truncated'): ('fragment_chat', False, None, False, True, False, False, False, False),
    ('chat', True, 'ip', 'incomplete'): ('fragment_clarify', False, None, False, False, False, False, False, False),
    ('chat', True, 'ip', 'unscored_groq'): ('fragment_chat', False, None, False, True, False, False, False, False),
}


class ProcessorGateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_combination_matches_snapshot(self):
        self.assertEqual(len(EXPECTED), len(list(cases())))
        for case in cases():
            with self.subTest(mode=case[0], natural=case[1], text=case[2], trust=case[3]):
                actual = await observe(*case)
                self.assertEqual(dict(zip(FIELDS, actual)), dict(zip(FIELDS, EXPECTED[case])))

    async def test_untrusted_turns_never_route_or_confirm_except_documented_stop(self):
        # Checked against live behaviour, independent of the snapshot rows.
        for mode, natural, text, trust in cases():
            if trust in ("reliable", "unscored_groq"):
                continue
            with self.subTest(mode=mode, natural=natural, text=text, trust=trust):
                outcome = dict(zip(FIELDS, await observe(mode, natural, text, trust)))
                self.assertFalse(outcome["routed"])
                self.assertFalse(outcome["confirm_asked"])
                self.assertFalse(outcome["memory_written"])
                self.assertFalse(outcome["document"])


if __name__ == "__main__":
    unittest.main()
