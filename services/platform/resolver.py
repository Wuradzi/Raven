"""OS facts and capability resolution, independent of STT/deployment profiles."""
from dataclasses import dataclass
import platform

CAPABILITIES = ('tts', 'application_launch', 'window_control', 'process_control',
                'session_control', 'power_control', 'file_open', 'drive_inventory', 'workplace',
                'media_control', 'screenshot')

# macOS: only what has a documented, permission-explained implementation in
# services/platform/macos.py. Window/process control, power control, workplace and
# drive inventory stay NOT_IMPLEMENTED (no safe equivalent without extra privileges).
DARWIN_CAPABILITIES = frozenset({'tts', 'application_launch', 'file_open', 'session_control',
                                 'media_control', 'screenshot'})
MANIFEST_ALIASES = {'macos': 'darwin', 'mac': 'darwin', 'win32': 'windows'}


def manifest_platform_matches(platforms):
    """True when a skill manifest `platforms` list covers this OS ('all' covers everything)."""
    current = platform.system().lower()
    normalized = {MANIFEST_ALIASES.get(str(item).lower(), str(item).lower()) for item in platforms}
    return current in normalized or 'all' in normalized


class PlatformOperationError(RuntimeError):
    def __init__(self, capability, status):
        self.capability = capability
        self.status = status
        super().__init__({'unsupported': 'Ця дія недоступна на цій платформі.',
                          'not_available': 'Ця дія недоступна в поточному середовищі.',
                          'not_found': 'Не знайдено потрібну програму.',
                          'permission_denied': 'Операційна система відмовила в доступі.',
                          'execution_failed': 'Не вдалося виконати дію.'}[status])


class PlatformCapabilityUnavailable(PlatformOperationError):
    def __init__(self, capability):
        super().__init__(capability, 'unsupported')


@dataclass(frozen=True)
class PlatformServices:
    os: str
    architecture: str

    def supports(self, capability):
        return (self.os == 'Windows' and capability in CAPABILITIES
                or self.os == 'Linux' and capability in {'application_launch', 'file_open'}
                or self.os == 'Darwin' and capability in DARWIN_CAPABILITIES)

    def report(self):
        result = dict(os=self.os, architecture=self.architecture,
                    capabilities={name: 'IMPLEMENTED' if self.supports(name) else 'NOT_IMPLEMENTED'
                                  for name in CAPABILITIES}, hardware_validation='NOT_TESTED')
        if self.os == 'Linux':
            from services.platform.linux import metadata
            result.update(metadata())
            if result['headless'] or not result['desktop_opener']:
                result['capabilities']['file_open'] = 'NOT_AVAILABLE'
        return result

    def _module(self):
        from importlib import import_module
        name = {'Windows': 'windows', 'Darwin': 'macos'}.get(self.os, 'linux')
        return import_module('services.platform.' + name)

    def _driver(self, capability):
        if not self.supports(capability):
            raise PlatformCapabilityUnavailable(capability)
        return self._module()

    def open_application(self, app):
        return self._driver('application_launch').open_application(app)

    def open_file(self, path):
        return self._driver('file_open').open_file(path)

    def drive_roots(self):
        # No desktop drive inventory on an unsupported target; explicit dirs remain usable.
        return self._driver('drive_inventory').drive_roots() if self.supports('drive_inventory') else []

    def windows(self):
        if not self.supports('window_control'):
            return UnsupportedWindows()
        return self._driver('window_control').window_controller()

    def system_action(self, action):
        capability = 'session_control' if action == 'lock' else 'power_control'
        return self._driver(capability).system_action(action)

    def speech(self, owner, text, path):
        from importlib import import_module
        self._driver('tts')
        if self.os == 'Darwin':
            return self._module().speech(owner, text, path)
        return import_module('services.platform.windows_tts').generate(owner, text, path)

    def start_speech(self, owner):
        self._driver('tts')
        if self.os == 'Darwin':
            return self._module().start_speech(owner)
        from services.platform.windows_tts import start
        return start(owner)

    def synthesize_probe(self, path, hint):
        self._driver('tts')
        if self.os == 'Darwin':
            return self._module().synthesize_probe(path, hint)
        from services.audio.windows_speech import synthesize_windows
        return synthesize_windows(path, hint)

    def stop_playback(self):
        # Windows keeps its own winsound purge in Speaker; only macOS tracks a player process.
        if self.os == 'Darwin':
            self._module().stop_playback()

    def media_key(self, action, steps=1):
        """macOS media/volume key. Windows keeps the pyautogui path in skills/media."""
        return self._driver('media_control').media_key(action, steps)

    def take_screenshot(self, path):
        return self._driver('screenshot').take_screenshot(path)

    def play_wav(self, path):
        return self._driver('tts').play_wav(path)


class UnsupportedWindows:
    def candidates(self, *args):
        raise PlatformCapabilityUnavailable('window_control')

    change_target = minimize = maximize = close = candidates

    def terminate_processes(self, *args):
        raise PlatformCapabilityUnavailable('process_control')


def resolve_platform():
    system = platform.system()
    machine = platform.machine().casefold()
    architecture = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'arm64',
                    'aarch64': 'arm64', 'armv7l': 'arm32', 'x86': 'x86', 'i386': 'x86'}.get(machine, 'unknown')
    return PlatformServices(system if system in {'Windows', 'Linux', 'Darwin'} else 'Unsupported', architecture)
