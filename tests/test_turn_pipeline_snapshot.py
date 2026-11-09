"""Roadmap phase 1 oracle: the turn pipeline refactor must not change these outcomes.

A difference is either a regression or an intended change; an intended change is
reviewed case by case, recorded in docs/BEHAVIORAL_CONTRACTS.md and the golden file
is regenerated (see tests/turn_pipeline_snapshot.py).
"""
import json
import unittest

from core.command_actions import UNSCORED_VOICE_TOOLS
from tests.turn_pipeline_snapshot import FIELDS, GOLDEN, snapshot

DAMAGED = {"unreliable", "truncated", "incomplete", "not_eligible", "unscored_cut"}


class TurnPipelineSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_and_trust_invariants(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(tuple(golden["fields"]), FIELDS)
        expected = golden["cases"]
        actual = await snapshot()
        self.assertEqual(set(expected) - set(actual), set(), "cases missing from the run")
        self.assertEqual(set(actual) - set(expected), set(), "new cases need a reviewed golden file")
        executed = FIELDS.index("executed")
        for name, row in actual.items():
            with self.subTest(case=name):
                diff = {field: (want, got) for field, want, got in zip(FIELDS, expected[name], row) if want != got}
                self.assertEqual(diff, {})
                # Contract, independent of the golden file: damaged speech reaches no executor;
                # clean unscored Groq voice reaches only UNSCORED_VOICE_TOOLS (the executor then
                # confirms every state change except UNCONFIRMED_TOOLS and window focus).
                trust = name.split("|")[0]
                if trust in DAMAGED:
                    self.assertEqual(row[executed], [])
                elif trust == "unscored_groq":
                    self.assertLessEqual(set(row[executed]), UNSCORED_VOICE_TOOLS)


if __name__ == "__main__":
    unittest.main()
