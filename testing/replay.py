"""Offline replay of PCM through the production Vosk capture loop (for tests; no microphone)."""
from __future__ import annotations

import queue
import time
from types import SimpleNamespace
from unittest.mock import patch


def replay(listener, pcm, rate):
    """Exercise production endpoint/energy code, feeding one block per get().

Queue has no backlog; real capture overflow/CPU scheduling are not simulated.
Append at most 4 s of digital silence to allow an endpoint after manual Stop.
"""
    import core.listen as module
    block = max(800, int(rate * getattr(listener.settings, 'stt_audio_block_ms', 250) / 1000)) * 2
    padded = pcm + b'\0\0' * (rate * 4)
    clock = {'offset': 0, 'time': 0.0, 'callback': None}
    class ReplayQueue:
        def __init__(self):
            self.item = None
        def put(self, item):
            self.item = item
        def empty(self):
            return self.item is None
        def get(self, timeout=None):
            if clock['offset'] >= len(padded):
                clock['time'] = 15.01
                raise queue.Empty
            data = padded[clock['offset']:clock['offset'] + block]
            clock['offset'] += len(data)
            clock['time'] = clock['offset'] / (2 * rate)
            clock['callback'](data, len(data) // 2, None, False)
            result, self.item = self.item, None
            return result
    class Stream:
        def __init__(self, **kwargs):
            clock['callback'] = kwargs['callback']
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    listener._interrupt.clear()
    with patch.object(module, 'queue', SimpleNamespace(Queue=ReplayQueue, Empty=queue.Empty)), \
            patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock['time'], perf_counter=time.perf_counter)):
        result, captured = listener._listen_on_device(SimpleNamespace(RawInputStream=Stream), 0, rate, 15, None)
    return result, captured
