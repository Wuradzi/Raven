from __future__ import annotations

import io
import unittest
import wave
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace


from core.listen import VoskListener
from core.models import RecognitionResult
from services.audio.whisper import WhisperRecognizer
from services.audio.refinement import RefinementWait
from config import Settings, ProjectPaths


class HybridRecognitionTests(unittest.TestCase):
    def test_audio_uses_policy_without_processor_callback(self):
        import inspect
        from core.recognition_policy import RecognitionPolicy, DialogueState
        source = inspect.getsource(VoskListener)
        for dependency in ('direct_request', 'voice_request', 'stt_scoped_reply', 'self.scoped_reply'):
            self.assertNotIn(dependency, source)
        self.assertIsInstance(self.listener.recognition_policy, RecognitionPolicy)
        self.assertIsInstance(self.listener.recognition_policy.dialogue, DialogueState)

    def setUp(self):
        self.listener = object.__new__(VoskListener)
        self.listener.settings = SimpleNamespace(
            stt_whisper_min_confidence=0.45,
            stt_whisper_skip_silence=True,
            noise_threshold=0.005,
        )

    def test_zero_filled_windows_endpoint_is_rejected(self):
        self.assertFalse(VoskListener._signal_present(b"\x00\x00" * 400))
        self.assertTrue(
            VoskListener._signal_present(b"\x00\x00\x10\x00" * 200)
        )

class SelectiveRefinementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.listener = VoskListener(Settings(
            ProjectPaths.from_root(Path(self.directory.name)), stt_selective_whisper_enabled=True,
            stt_endpoint_adaptive=True, stt_whisper_soft_budget_ms=500, stt_whisper_hard_budget_ms=1000))
        self.pcm = b'\x00\x10' * 16000
        self.listener._input_candidates = Mock(return_value=[(0, 16000)])
        self.listener.whisper = Mock(enabled=True)
        self.listener.whisper.transcribe.return_value = RecognitionResult('Зрозуміла відповідь', .9, 'whisper')

    def capture(self, text, confidence=.96, **flags):
        self.listener._listen_on_device = Mock(return_value=(RecognitionResult(text, confidence, **flags), self.pcm))

    def test_scoped_hint_uses_existing_pending_context_and_catalogue(self):
        import time
        from core.processor import CommandProcessor
        processor = object.__new__(CommandProcessor)
        processor.services = {'apps': SimpleNamespace(has_exact_name=lambda name: name == 'Telegram')}
        pending = {'tool': 'open_app', 'expires': time.monotonic() + 60}
        processor._natural_pending = pending
        self.assertTrue(processor.stt_scoped_reply('Telegram'))
        self.assertFalse(processor.stt_scoped_reply('невідома програма'))
        self.assertFalse(processor.stt_scoped_reply('відкрий Telegram'))
        self.assertIs(processor._natural_pending, pending)
        pending['expires'] = 0
        self.assertFalse(processor.stt_scoped_reply('Telegram'))

    def test_busy_cancelled_and_late_results_do_not_start_parallel_jobs(self):
        waiter = RefinementWait()
        release = threading.Event()
        recognizer = Mock()
        recognizer.transcribe.side_effect = lambda *args: (release.wait(2), RecognitionResult('late', .9, 'whisper'))[1]
        try:
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: False)[1], 'timeout')
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: False)[1], 'busy')
            recognizer.transcribe.assert_called_once()
        finally:
            release.set()
            self.assertTrue(waiter._active.wait(1))
        release.clear()
        try:
            self.assertEqual(waiter.run(recognizer, b'', 16000, 5, 20, lambda: True)[1], 'cancelled')
        finally:
            release.set()
            self.assertTrue(waiter._active.wait(1))

class WhisperAudioTests(unittest.TestCase):
    def test_pcm_is_wrapped_as_valid_mono_wav(self):
        pcm = b"\x00\x00\x01\x00" * 80
        buffer = WhisperRecognizer._wav_buffer(pcm, 48_000)
        self.assertIsInstance(buffer, io.BytesIO)
        with wave.open(buffer, "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), 48_000)
            self.assertEqual(audio.readframes(audio.getnframes()), pcm)
