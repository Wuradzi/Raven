"""macOS execution only; authorization stays in Core. No shell, no sudo.

Every call uses an argv list (shell=False). Nothing here is validated on real macOS
hardware yet (see docs/MACOS_SETUP.md): the offline tests mock subprocess/Quartz.
Neither arguments, environment nor stderr are logged.
"""
import functools
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time

from services.platform.resolver import PlatformCapabilityUnavailable, PlatformOperationError

logger = logging.getLogger(__name__)

# Exposed for tests and docs; the tools are standard macOS command-line utilities.
OPEN, OSASCRIPT, SAY, AFPLAY, SCREENCAPTURE = 'open', 'osascript', 'say', 'afplay', 'screencapture'
VOLUME_STEP_PERCENT = 6  # one hardware key press is 1/16 of the range (~6%)
MEDIA_TRANSPORT_KEYS = {'play_pause': 16, 'next_track': 17, 'previous_track': 18}
PERMISSION_MARKERS = ('not allowed assistive access', 'not allowed to send keystrokes', '(-1743)', '(1002)')
_VOICE_LINE = re.compile(r'^(?P<name>.+?)\s+(?P<locale>[a-z]{2}_[A-Z]{2})\s+#')

_player = None
_player_lock = threading.Lock()


def metadata():
    return dict(headless=False, desktop_opener=bool(shutil.which(OPEN)))


def _fail(capability, status, reason):
    logger.warning('macOS operation capability=%s status=%s reason=%s', capability, status, reason)
    raise PlatformOperationError(capability, status)


def _run(argv, capability, timeout=8):
    """Run one fixed utility; map OS errors to the fixed platform error set."""
    try:
        result = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except PermissionError:
        _fail(capability, 'permission_denied', 'os_permission')
    except FileNotFoundError:
        _fail(capability, 'not_found', 'executable_missing')
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(capability, 'execution_failed', type(exc).__name__)
    if result.returncode:
        stderr = (result.stderr or b'').decode('utf-8', 'replace').casefold()
        if any(marker in stderr for marker in PERMISSION_MARKERS):
            _fail(capability, 'permission_denied', 'macos_privacy_permission')
        _fail(capability, 'execution_failed', f'exit_{result.returncode}')
    return result


def _applescript(lines, capability):
    argv = [OSASCRIPT]
    for line in lines:
        argv += ['-e', line]
    return _run(argv, capability)


# ---- applications and files -------------------------------------------------

def _launch_target(command):
    """`.app` bundle path, or an application name for `open -a`, or None for a plain executable."""
    stripped = command.strip().rstrip('/')
    if stripped.casefold().endswith('.app'):
        return [OPEN, stripped]
    try:
        first = shlex.split(command)[0]
    except (ValueError, IndexError):
        return None
    if shutil.which(first) or (os.path.isabs(first) and os.access(first, os.X_OK)):
        return None
    return [OPEN, '-a', command.strip()]


def open_application(app):
    capability = 'application_launch'
    command = str(app.get('command', '')) if isinstance(app, dict) else ''
    if not command.strip():
        _fail(capability, 'not_found', 'empty_command')
    argv = _launch_target(command)
    if argv:
        _run(argv, capability)  # `open` exits 0 once LaunchServices accepted the request.
        return True  # Launch accepted, NOT a verified visible application.
    argv = shlex.split(command)
    argv[0] = shutil.which(argv[0]) or argv[0]
    try:
        process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        if process.poll() not in (None, 0):
            _fail(capability, 'execution_failed', 'early_exit')
    except PermissionError:
        _fail(capability, 'permission_denied', 'os_permission')
    except FileNotFoundError:
        _fail(capability, 'not_found', 'executable_disappeared')
    except OSError as exc:
        _fail(capability, 'execution_failed', type(exc).__name__)
    return True


def open_file(path):
    target = str(path)
    if target.startswith('-'):
        target = './' + target  # never let a file name become an option of `open`
    _run([OPEN, target], 'file_open')
    return True  # Opener accepted; no window verification on macOS.


# ---- session, media, screenshot --------------------------------------------

def system_action(action):
    """Only the screen lock exists: shutdown needs privileges, so it stays unsupported."""
    if action != 'lock':
        raise PlatformCapabilityUnavailable('power_control')
    # Ctrl+Cmd+Q is the system "Lock Screen" shortcut. Needs Accessibility permission
    # for the app hosting Raven (Terminal, iTerm, ...).
    _applescript(['tell application "System Events" to keystroke "q" using {control down, command down}'],
                 'session_control')
    return True  # Keystroke accepted; the lock itself is not verified.


def _volume_script(delta):
    return ['set v to (output volume of (get volume settings)) + (' + str(delta) + ')',
            'if v > 100 then set v to 100', 'if v < 0 then set v to 0', 'set volume output volume v']


def _post_transport_key(code):
    try:
        import AppKit
        import Quartz
    except ImportError:
        _fail('media_control', 'not_available', 'pyobjc_missing')
    for down in (True, False):
        data1 = (code << 16) | ((0xA if down else 0xB) << 8)
        event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            AppKit.NSEventTypeSystemDefined, (0, 0), 0xA00 if down else 0xB00, 0, 0, None, 8, data1, -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())


def media_key(action, steps=1):
    capability = 'media_control'
    steps = max(1, min(10, int(steps)))
    if action in ('volume_up', 'volume_down'):
        sign = 1 if action == 'volume_up' else -1
        _applescript(_volume_script(sign * VOLUME_STEP_PERCENT * steps), capability)
    elif action == 'mute_toggle':
        _applescript(['set volume output muted (not (output muted of (get volume settings)))'], capability)
    elif action in MEDIA_TRANSPORT_KEYS:
        _post_transport_key(MEDIA_TRANSPORT_KEYS[action])
    else:
        raise PlatformCapabilityUnavailable(capability)
    return True  # Sent, not verified.


def take_screenshot(path):
    from services.platform.macos_permissions import screen_recording_status
    if screen_recording_status() == 'denied':
        _fail('screenshot', 'permission_denied', 'screen_recording_denied')
    path = Path(path)
    _run([SCREENCAPTURE, '-x', '-t', 'png', str(path)], 'screenshot', timeout=15)
    return path.is_file() and path.stat().st_size > 0


# ---- speech -----------------------------------------------------------------

def _installed_voices():
    try:
        result = subprocess.run([SAY, '-v', '?'], shell=False, capture_output=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    voices = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        match = _VOICE_LINE.match(line)
        if match:
            voices.append((match['name'].strip(), match['locale']))
    return voices


@functools.lru_cache(maxsize=8)
def resolve_voice(hint):
    """(voice name or None, locale or None, fallback). Hint first, then any Ukrainian voice."""
    voices = _installed_voices()
    wanted = (hint or '').strip().casefold()
    if wanted:
        for name, locale in voices:
            if wanted in name.casefold():
                return name, locale, False
    for name, locale in voices:
        if locale.startswith('uk_'):
            return name, locale, bool(wanted)
    return None, None, bool(wanted)  # system default voice; may not read Ukrainian


def _say_argv(voice, rate, path=None):
    argv = [SAY]
    if voice:
        argv += ['-v', voice]
    argv += ['-r', str(max(90, min(400, int(rate))))]
    if path is not None:
        argv += ['-o', str(Path(path).resolve()), '--file-format=WAVE', '--data-format=LEI16@22050']
    return argv + ['-f', '-']  # text on stdin: it can never be parsed as an option


# Speaker glow on macOS. `say` plays straight to the speakers and exposes no PCM, so the level comes from a second,
# low-priority `say -o` of the same phrase (real PCM of the same voice, not a fake animation). It is ready ~0.7 s
# after the phrase starts; the frame is then picked by wall-clock time. GLOW_AUDIO_LATENCY_S is the approximate delay
# between spawning the live `say` and its first sound. The audible path is untouched.
GLOW_WINDOW_S = 0.05
GLOW_AUDIO_LATENCY_S = 0.6
GLOW_GAIN = 3.0


def glow_frame(samples, rate, elapsed):
    """(level 0..100, 32 values -100..100) for the 50 ms window at `elapsed` seconds, or None outside the audio."""
    import numpy as np
    start = int(elapsed * rate)
    chunk = samples[max(0, start):max(0, start) + max(1, int(rate * GLOW_WINDOW_S))]
    if elapsed < 0 or start >= len(samples) or len(chunk) == 0:
        return None
    scaled = chunk.astype(float) / 32768.0 * 100.0 * GLOW_GAIN
    level = int(min(100, np.sqrt(np.mean(scaled ** 2))))
    points = scaled[np.linspace(0, len(scaled) - 1, 32).astype(int)]
    return level, [int(v) for v in np.clip(points, -100, 100)]


def _read_wav_samples(path):
    import wave
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError('unexpected WAV format')
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2'), wav.getframerate()


def _glow_meter(owner, text, voice, live, started):
    """Feed owner._speech_audio from a shadow synthesis while the live `say` plays. Never raises, never blocks speech."""
    import tempfile
    shadow = None
    try:
        with tempfile.TemporaryDirectory(prefix='raven-glow-') as folder:
            path = Path(folder) / 'glow.wav'
            shadow = subprocess.Popen(['nice', '-n', '10', *_say_argv(voice, owner.settings.tts_rate, path)], shell=False,
                                      stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            shadow.stdin.write(text.encode('utf-8'))
            shadow.stdin.close()
            while shadow.poll() is None:
                if live.poll() is not None or owner._stop_requested.is_set() or owner._closing:
                    return
                time.sleep(0.02)
            if shadow.returncode:
                return
            samples, rate = _read_wav_samples(path)
            while live.poll() is None and not owner._stop_requested.is_set() and not owner._closing:
                elapsed = time.monotonic() - started - GLOW_AUDIO_LATENCY_S
                if elapsed >= len(samples) / rate:
                    return
                frame = glow_frame(samples, rate, elapsed)
                if frame is not None:
                    owner._speech_audio(*frame)
                time.sleep(GLOW_WINDOW_S)
    except Exception as exc:  # cosmetic only: the voice must never depend on the glow
        logging.getLogger(__name__).debug('speech glow stopped: %s', type(exc).__name__)
    finally:
        if shadow is not None and shadow.poll() is None:
            shadow.terminate()


def _wait(owner, process, deadline):
    while process.poll() is None:
        if owner._stop_requested.is_set() or owner._closing:
            return False
        if time.monotonic() >= deadline:
            owner._stop_sync()
            raise RuntimeError('Тайм-аут say')
        time.sleep(0.05)
    return True


def speech(owner, text, path):
    """Speaker transport: `say` reads the phrase from stdin; WAV for the cache or live playback."""
    if not text.strip():
        return  # Speaker.prepare() warm-up; nothing resident to start on macOS.
    voice = resolve_voice(owner.settings.tts_voice_hint)[0]
    argv = _say_argv(voice, owner.settings.tts_rate, path)
    with owner._lock:
        with owner._process_lock:
            if owner._closing or owner._stop_requested.is_set():
                return
            process = subprocess.Popen(argv, shell=False, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            owner._tts_process = process
        if path is None:
            owner._set_playback(True)
            threading.Thread(target=_glow_meter, args=(owner, text, voice, process, time.monotonic()),
                             daemon=True, name='speech-glow').start()
        if owner._current_timing is not None:
            owner._current_timing.mark('tts_submit' if path is None else 'tts_file_synthesis')
        try:
            process.stdin.write(text.encode('utf-8'))
            process.stdin.close()
        except OSError:
            pass  # The wait below reports the exit code.
        if _wait(owner, process, time.monotonic() + max(30, len(text) // 8)) and process.returncode:
            raise RuntimeError(f'say завершився з кодом {process.returncode}')


def start_speech(owner):
    return None  # No resident process on macOS.


def synthesize_probe(path, hint):
    voice, locale, fallback = resolve_voice(hint)
    data = dict(status='ok', voice=voice or 'system default', culture=locale, fallback=fallback,
                stage='synthesis_start', error_type=None, exception_message=None, exit_code=None)
    try:
        result = subprocess.run(_say_argv(voice, 185, path), input='Перевірка голосу.'.encode('utf-8'),
                                shell=False, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(data, status='synthesis_failed', error_type=type(exc).__name__)
    if result.returncode:
        return dict(data, status='synthesis_failed', exit_code=result.returncode)
    return data


def play_wav(path):
    global _player
    with _player_lock:
        _player = subprocess.Popen([AFPLAY, str(path)], shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process = _player
    process.wait()


def stop_playback():
    with _player_lock:
        process = _player
    if process is not None and process.poll() is None:
        process.terminate()
