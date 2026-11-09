"""macOS speaker glow: real PCM levels from a shadow `say -o`, audible path untouched."""
import struct
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from services.platform import macos


def write_wav(path, seconds=0.6, rate=22050, amplitude=0.3):
    t = np.arange(int(seconds * rate)) / rate
    data = (np.sin(2 * np.pi * 220 * t) * amplitude * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(data.tobytes())


class FrameTests(unittest.TestCase):
    def test_frame_has_valid_range_and_shape_for_the_speaker_validation(self):
        samples = (np.sin(np.arange(22050) / 8) * 20000).astype('<i2')
        level, points = macos.glow_frame(samples, 22050, 0.2)
        self.assertTrue(type(level) is int and 0 <= level <= 100)
        self.assertEqual(len(points), 32)
        self.assertTrue(all(type(v) is int and -100 <= v <= 100 for v in points))

    def test_silence_is_level_zero_and_outside_the_audio_is_none(self):
        silence = np.zeros(22050, dtype='<i2')
        self.assertEqual(macos.glow_frame(silence, 22050, 0.1)[0], 0)
        self.assertIsNone(macos.glow_frame(silence, 22050, 1.5))
        self.assertIsNone(macos.glow_frame(silence, 22050, -0.1))

    def test_louder_audio_gives_higher_level(self):
        quiet = (np.sin(np.arange(22050) / 8) * 1500).astype('<i2')
        loud = (np.sin(np.arange(22050) / 8) * 20000).astype('<i2')
        self.assertLess(macos.glow_frame(quiet, 22050, 0.1)[0], macos.glow_frame(loud, 22050, 0.1)[0])


class FakeShadow:
    """Stands in for the shadow `say -o`: writes the WAV the real one would."""
    last_argv = None

    def __init__(self, argv, **kwargs):
        FakeShadow.last_argv = argv
        write_wav(argv[argv.index('-o') + 1])
        self.stdin = Mock()
        self.returncode = 0

    def poll(self):
        return 0

    def terminate(self):
        pass


class Live:
    def __init__(self, polls):
        self.polls = polls

    def poll(self):
        self.polls -= 1
        return None if self.polls > 0 else 0


def owner():
    return SimpleNamespace(settings=SimpleNamespace(tts_rate=185), _stop_requested=threading.Event(),
                           _closing=False, _speech_audio=Mock())


class MeterTests(unittest.TestCase):
    def run_meter(self, own, live):
        with patch.object(macos.subprocess, 'Popen', FakeShadow), patch.object(macos, 'GLOW_AUDIO_LATENCY_S', 0.0), \
                patch.object(macos.time, 'sleep', lambda s: None):
            macos._glow_meter(own, 'Привіт', 'Lesya', live, macos.time.monotonic())

    def test_meter_feeds_valid_frames_while_the_live_say_plays(self):
        own = owner()
        self.run_meter(own, Live(6))
        self.assertGreaterEqual(own._speech_audio.call_count, 1)
        for call in own._speech_audio.call_args_list:
            level, points = call.args
            self.assertTrue(type(level) is int and 0 <= level <= 100 and len(points) == 32)
        argv = FakeShadow.last_argv
        self.assertEqual(argv[:3], ['nice', '-n', '10'])  # never competes with the live voice
        self.assertIn('-o', argv)

    def test_stop_request_ends_the_meter_without_frames(self):
        own = owner()
        own._stop_requested.set()
        self.run_meter(own, Live(50))
        own._speech_audio.assert_not_called()

    def test_meter_failure_never_raises(self):
        own = owner()
        with patch.object(macos.subprocess, 'Popen', side_effect=OSError('no nice')):
            macos._glow_meter(own, 'Привіт', 'Lesya', Live(3), 0.0)
        own._speech_audio.assert_not_called()


class SpeechStartsMeterTests(unittest.TestCase):
    def test_live_speech_starts_the_meter_but_cached_synthesis_does_not(self):
        for path, expected in ((None, 1), (Path('/tmp/x.wav'), 0)):
            own = SimpleNamespace(settings=SimpleNamespace(tts_rate=185, tts_voice_hint=''), _lock=threading.RLock(),
                                  _process_lock=threading.Lock(), _closing=False, _stop_requested=threading.Event(),
                                  _set_playback=Mock(), _current_timing=None, _tts_process=None)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            with patch.object(macos.subprocess, 'Popen', return_value=process), \
                    patch.object(macos, 'resolve_voice', return_value=('Lesya', 'uk_UA', False)), \
                    patch.object(macos.threading, 'Thread') as thread:
                macos.speech(own, 'Привіт', path)
            self.assertEqual(thread.call_count, expected, path)


if __name__ == '__main__':
    unittest.main()
