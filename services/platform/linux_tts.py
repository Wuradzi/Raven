"""Local RHVoice synthesis and cancellable ALSA playback on Linux."""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def _executable(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Не знайдено {name}. Див. інструкцію Linux TTS.")
    return path


def _run(command, *, timeout, stop_requested=None, input_bytes=None):
    if stop_requested is not None and stop_requested.is_set():
        return False
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"{Path(command[0]).name} перевищив час очікування.")
                try:
                    process.communicate(input_bytes, timeout=min(remaining, 0.05))
                    break
                except subprocess.TimeoutExpired:
                    if stop_requested is not None and stop_requested.is_set():
                        return False
                    input_bytes = None  # communicate() retains the original bytes after timeout.
            if stop_requested is not None and stop_requested.is_set():
                return False
            if process.returncode:
                raise RuntimeError(f"{Path(command[0]).name} завершився з кодом {process.returncode}.")
            return True
        finally:
            if process.poll() is None:
                process.kill()


def _voice(hint):
    voice = (hint or '').strip()
    return 'Volodymyr' if voice.casefold() in {'', 'volodymyr', 'володимир'} else voice


def _synthesize(text, path, voice, *, rate=185, volume=1.0, stop_requested=None):
    rate_percent = max(1, round(rate * 100 / 185))
    volume_percent = max(0, min(100, round(volume * 100)))
    command = [_executable('RHVoice-test'), '-p', voice, '-r', str(rate_percent),
               '-v', str(volume_percent), '-o', str(path)]
    try:
        return _run(command, timeout=20, stop_requested=stop_requested, input_bytes=text.encode('utf-8'))
    except RuntimeError as exc:
        raise RuntimeError(f"{exc} Перевірте, чи встановлено голос RHVoice «{voice}».") from exc


def play_wav(path, stop_requested):
    return _run([_executable('aplay'), '-q', str(path)], timeout=60, stop_requested=stop_requested)


def speech(owner, text, path):
    """Synthesize to a requested path or play a private, temporary WAV."""
    if not text.strip() or owner._stop_requested.is_set():
        return
    settings = owner.settings
    options = dict(rate=settings.tts_rate, volume=settings.tts_volume, stop_requested=owner._stop_requested)
    if path is not None:
        _synthesize(text, path, _voice(settings.tts_voice_hint), **options)
        return
    with tempfile.TemporaryDirectory(prefix='raven-linux-tts-') as folder:
        wav = Path(folder) / 'speech.wav'
        if not _synthesize(text, wav, _voice(settings.tts_voice_hint), **options):
            return
        if not owner._is_valid_wav(wav):
            raise RuntimeError("RHVoice не створив придатний WAV.")
        if not owner._stop_requested.is_set():
            owner._play_wav(wav)


def synthesize_probe(path, hint):
    if shutil.which('RHVoice-test') is None:
        return {'status': 'backend_unavailable', 'exception_message': 'Не знайдено RHVoice-test.'}
    voice = _voice(hint)
    _synthesize("Перевірка генерації голосу Raven", path, voice)
    return {'status': 'ok', 'voice': voice, 'culture': 'uk-UA' if voice == 'Volodymyr' else None}
