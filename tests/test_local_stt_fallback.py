"""Phase 8: optional lazy local Vosk reserve, used only while the cloud recognizer (Groq) is failing."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from config import ConfigError, default_config, _validate_local_fallback
from core.models import RecognitionResult
from core.stt_listener import FALLBACK_NOTICES, SpeechListener

PCM = SimpleNamespace(pcm=b'\0' * 3200, sample_rate=16000)


def listener(*, backend='groq', enabled=True, maximum=3, legacy='none', vosk=None):
    obj = SpeechListener.__new__(SpeechListener)
    obj.settings = SimpleNamespace(stt_local_fallback_enabled=enabled, stt_local_fallback_max_consecutive=maximum,
                                   stt_fallback_backend=legacy)
    obj.profile = SimpleNamespace(backend=backend)
    obj.vosk_backend = vosk or Mock()
    obj._cloud_outage_streak = 0
    obj._fallback_notice = None
    return obj


def vosk_returning(text, confidence=0.9):
    vosk = Mock()
    vosk.transcribe.return_value = RecognitionResult(text, confidence, 'vosk')
    return vosk


class ConfigTests(unittest.TestCase):
    def test_off_by_default_and_validated(self):
        section = default_config()['stt']['local_fallback']
        self.assertEqual(section, {'enabled': False, 'max_consecutive': 5})
        _validate_local_fallback(section)
        for bad in ({'enabled': 'yes', 'max_consecutive': 5}, {'enabled': True, 'max_consecutive': 0},
                    {'enabled': True, 'max_consecutive': 51}, {'enabled': True, 'max_consecutive': True},
                    {'enabled': True}, {'enabled': True, 'max_consecutive': 5, 'x': 1}, [], None):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                _validate_local_fallback(bad)


class ReserveTests(unittest.TestCase):
    def test_disabled_by_default_never_touches_vosk(self):
        vosk = Mock()
        obj = listener(enabled=False, vosk=vosk)
        result = RecognitionResult('', 0, 'groq')
        self.assertEqual(obj._fallback_result(result, PCM, 'timeout'), (result, True))
        vosk.transcribe.assert_not_called()

    def test_only_cloud_outage_reasons_use_the_reserve(self):
        for reason in ('authorization', 'missing_key', 'empty_or_unreliable', 'non_ukrainian_transcript',
                       'subtitle_hallucination', 'invalid_audio', 'cancelled'):
            vosk = vosk_returning('так')
            obj = listener(vosk=vosk)
            result = RecognitionResult('', 0, 'groq')
            with self.subTest(reason=reason):
                self.assertEqual(obj._fallback_result(result, PCM, reason), (result, True))
                vosk.transcribe.assert_not_called()

    def test_outage_uses_vosk_result_with_its_own_confidence_and_notifies_once(self):
        vosk = vosk_returning('відкрий браузер', 0.91)
        obj = listener(maximum=10, vosk=vosk)
        for reason in ('timeout', 'network', 'rate_limit', 'server'):
            result, failed = obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, reason)
            self.assertEqual((result.engine, result.text, result.confidence, failed), ('vosk', 'відкрий браузер', 0.91, False))
        self.assertEqual(obj.take_notice(), FALLBACK_NOTICES['engaged'])
        self.assertIsNone(obj.take_notice())  # reading clears it; no repeat while the outage lasts

    def test_empty_or_non_finite_reserve_result_stays_unusable(self):
        for vosk in (vosk_returning(''), vosk_returning('так', float('nan'))):
            result, failed = listener(vosk=vosk)._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
            self.assertTrue(failed)

    def test_missing_local_model_is_reported_honestly(self):
        vosk = Mock()
        vosk.transcribe.side_effect = FileNotFoundError('no model')
        obj = listener(vosk=vosk)
        result, failed = obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'network')
        self.assertTrue(failed)
        self.assertEqual(result.text, '')
        self.assertEqual(obj.take_notice(), FALLBACK_NOTICES['local_unavailable'])

    def test_limit_of_consecutive_attempts_then_silence_until_cloud_recovers(self):
        vosk = vosk_returning('так')
        obj = listener(maximum=2, vosk=vosk)
        for _ in range(2):
            obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
        obj.take_notice()
        self.assertEqual(vosk.transcribe.call_count, 2)
        result, failed = obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
        self.assertTrue(failed)
        self.assertEqual(vosk.transcribe.call_count, 2)
        self.assertEqual(obj.take_notice(), FALLBACK_NOTICES['exhausted'])
        obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
        self.assertIsNone(obj.take_notice())
        obj._note_primary_outcome('done', 'empty_or_unreliable')  # Groq answered again
        obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
        self.assertEqual(vosk.transcribe.call_count, 3)

    def test_reserve_result_or_outage_does_not_reset_the_streak(self):
        obj = listener(vosk=vosk_returning('так'))
        obj._fallback_result(RecognitionResult('', 0, 'groq'), PCM, 'timeout')
        obj._note_primary_outcome('done', 'timeout')
        obj._note_primary_outcome('timeout', 'timeout')
        self.assertEqual(obj._cloud_outage_streak, 1)
        obj._note_primary_outcome('done', 'empty_or_unreliable')
        self.assertEqual(obj._cloud_outage_streak, 0)

    def test_legacy_non_cloud_fallback_is_unchanged(self):
        vosk = vosk_returning('так')
        obj = listener(backend='faster-whisper', enabled=False, legacy='vosk', vosk=vosk)
        result, failed = obj._fallback_result(RecognitionResult('', 0, 'whisper'), PCM, 'empty_or_unreliable')
        self.assertEqual((result.engine, failed), ('vosk', True))  # still counts as unreliable, as before
        self.assertIsNone(obj.take_notice())

    def test_reserve_is_not_loaded_while_groq_works(self):
        vosk = Mock()
        obj = listener(vosk=vosk)
        obj.primary = Mock()
        obj.primary.prepare.return_value = (True, 'ok')
        obj.prepare()
        vosk.prepare.assert_not_called()
        vosk.get_model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
