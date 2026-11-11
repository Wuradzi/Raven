"""Offline checks for testing/session_metrics.py: counts, percentiles and privacy of the report."""
import tempfile
import unittest
from pathlib import Path

from testing.session_metrics import Metrics, percentile, render

LOG = """\
2026-11-09 17:50:56,368 | INFO | x | session.stdout | [VOICE/groq unscored] SECRET PHRASE
2026-11-09 17:50:57,287 | INFO | x | core.app | turn.route source=voice engine=groq scored=False eligible=False type=open_application route=none natural_action=True
2026-11-09 17:50:57,289 | INFO | x | core.performance | [TURN] endpoint=1200ms | recognition_ready=400ms | stt=n/a | llm_first_text=900ms | first_phrase=1500ms | tts_submit=n/a | total=2600ms
2026-11-09 17:51:01,000 | INFO | x | core.app | turn.route source=text engine=text scored=True eligible=True type=chat route=none natural_action=False
2026-11-09 17:51:02,000 | INFO | x | core.app | voice_input_discarded reason=empty_transcript engine=whisper
2026-11-09 17:51:03,000 | INFO | x | g | stt.groq.complete provider=groq api_roundtrip_ms=500.4 success=True error_class=none http_status=200
2026-11-09 17:51:04,000 | INFO | x | g | stt.groq.complete provider=groq api_roundtrip_ms=900.0 success=False error_class=timeout http_status=0
2026-11-09 17:51:05,000 | INFO | x | core.confirmation | confirmation_reply_rejected=stale_or_uncorrelated
2026-11-09 17:51:06,000 | INFO | x | core.app | confirmation_button outcome=confirm source=ui request=deadbeef
"""


class SessionMetricsTests(unittest.TestCase):
    def aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session-20261109T175048.000000Z-1-a.log"
            path.write_text(LOG, encoding="utf-8")
            metrics = Metrics()
            metrics.add_file(path)
        return metrics.summary()

    def test_counts(self):
        s = self.aggregate()
        self.assertEqual((s["voice_turns"], s["text_turns"], s["voice_unscored"]), (1, 1, 1))
        self.assertEqual(s["voice_natural_action"], 1)
        self.assertEqual(s["discarded_reasons"], {"empty_transcript": 1})
        self.assertEqual(s["groq_requests"], {"ok": 1, "failed": 1})
        self.assertEqual(s["groq_errors"], {"timeout": 1})
        self.assertEqual(s["confirmation_rejected"], {"stale_or_uncorrelated": 1})
        self.assertEqual(s["confirmation_button"], {"confirm": 1})
        self.assertEqual(s["turn_ms"]["tts_submit"]["n"], 0)
        self.assertEqual(s["turn_ms"]["first_phrase"]["p50"], 1500)

    def test_report_has_no_transcript_or_request_id(self):
        text = render(self.aggregate())
        self.assertNotIn("SECRET PHRASE", text)
        self.assertNotIn("deadbeef", text)

    def test_percentile_nearest_rank(self):
        self.assertIsNone(percentile([], 0.5))
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2)
        self.assertEqual(percentile(list(range(1, 21)), 0.95), 19)


if __name__ == "__main__":
    unittest.main()
