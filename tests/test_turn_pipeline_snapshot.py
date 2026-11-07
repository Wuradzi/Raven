"""Roadmap phase 1 oracle: the turn pipeline refactor must not change these outcomes.

A difference is either a regression or an intended change; an intended change is
reviewed case by case, recorded in docs/BEHAVIORAL_CONTRACTS.md and the golden file
is regenerated (see tests/turn_pipeline_snapshot.py).
"""
import json
import unittest

from tests.turn_pipeline_snapshot import FIELDS, GOLDEN, key, cases, observe


class TurnPipelineSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_case_matches_the_reviewed_snapshot(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(tuple(golden["fields"]), FIELDS)
        expected = golden["cases"]
        seen = set()
        for case in cases():
            name = key(*case)
            seen.add(name)
            actual = await observe(*case)
            with self.subTest(case=name):
                self.assertIn(name, expected)
                diff = {field: (want, got) for field, want, got in zip(FIELDS, expected[name], actual)
                        if want != got}
                self.assertEqual(diff, {})
        self.assertEqual(set(expected) - seen, set())

    async def test_damaged_or_unscored_voice_never_executes_a_state_change(self):
        # Contract, independent of the golden file: only UNCONFIRMED_TOOLS may run without
        # a scored, eligible turn, and never from damaged speech.
        for case in cases():
            trust, _natural, _text, _reply = case
            if trust in {"unreliable", "truncated", "incomplete", "not_eligible"}:
                with self.subTest(case=key(*case)):
                    self.assertEqual((await observe(*case))[2], [])
            elif trust == "unscored_groq":
                with self.subTest(case=key(*case)):
                    executed = (await observe(*case))[2]
                    self.assertLessEqual(set(executed), {"open_app", "find_files", "weather", "web_search"})


if __name__ == "__main__":
    unittest.main()
