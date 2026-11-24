"""Mocked platform facts and execution contracts; no Linux/Pi hardware claim."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from services.platform.resolver import resolve_platform, PlatformServices, PlatformCapabilityUnavailable


class PlatformBoundaryTests(unittest.TestCase):
    def test_resolver_facts_not_pi_detection(self):
        for os_name, arch, expected in [('Windows', 'AMD64', 'x86_64'), ('Linux', 'x86_64', 'x86_64'),
                                         ('Linux', 'aarch64', 'arm64'), ('Windows', 'ARM64', 'arm64')]:
            with patch('platform.system', return_value=os_name), patch('platform.machine', return_value=arch):
                target = resolve_platform()
            self.assertEqual((target.os, target.architecture), (os_name, expected))
            self.assertTrue(target.supports('tts'))
            self.assertIsNot(target.report().get('raspberry_pi'), True)

    def test_linux_core_import_does_not_load_windows_implementations(self):
        script = '''
import platform, sys
platform.system = lambda: 'Linux'
platform.machine = lambda: 'aarch64'
import main, core.app, core.speak, services.health.probes
assert 'services.audio.windows_speech' not in sys.modules
assert 'services.platform.windows_tts' not in sys.modules
assert 'services.apps.workplace_windows' not in sys.modules
assert 'win32com.client' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_launch_contract(self):
        target = PlatformServices('Windows', 'x86_64')
        app = {'name': 'fixture', 'command': 'fixture.exe'}
        with patch('services.platform.windows.os.startfile', create=True) as launch:
            self.assertTrue(target.open_application(app))
            launch.assert_called_once_with('fixture.exe')

    def test_unsupported_never_success_or_shell_execution(self):
        target = PlatformServices('Linux', 'arm64')
        with patch('subprocess.Popen') as spawn:
            for operation in (lambda: target.system_action('shutdown'),
                              lambda: target.windows().close('anything'), lambda: target.media_key('pause')):
                with self.assertRaises(PlatformCapabilityUnavailable):
                    operation()
            spawn.assert_not_called()
        self.assertEqual(target.drive_roots(), [])

    def test_windows_probe_uses_stabilized_synthesis(self):
        target = PlatformServices('Windows', 'x86_64')
        with patch('services.audio.windows_speech.synthesize_windows', return_value={'status': 'ok'}) as synth:
            self.assertEqual(target.synthesize_probe('fixture.wav', 'Volodymyr'), {'status': 'ok'})
            synth.assert_called_once_with('fixture.wav', 'Volodymyr')

    def test_platform_report_contains_only_fixed_metadata(self):
        with patch('platform.system', return_value='SECRET_OS'), patch('platform.machine', return_value='SECRET_ARCH'):
            text = json.dumps(resolve_platform().report())
        self.assertNotIn('SECRET', text)
        self.assertIn('NOT_IMPLEMENTED', text)
        self.assertIn('NOT_TESTED', text)

    def test_stt_profiles_not_selected_by_platform_adapter(self):
        from config import Settings, ProjectPaths
        from services.audio.profiles import resolve_profile
        settings = Settings(ProjectPaths.from_root(Path.cwd()))
        caps = SimpleNamespace(architecture='aarch64', cuda=False, ram_mb=2048, vram_mb=0)
        for name in ('quality', 'balanced', 'edge'):
            settings.stt_profile = name
            with patch('platform.system', return_value='Linux'):
                selected, _ = resolve_profile(settings, caps)
            self.assertEqual(selected.name, name)
        self.assertEqual(PlatformServices('Linux', 'arm64').report()['hardware_validation'], 'NOT_TESTED')

    def test_confirmation_stays_before_platform_execution(self):
        from skills.system.skill import handle
        target = Mock()
        context = SimpleNamespace(confirm=AsyncMock(return_value=False))
        with patch('skills.system.skill.resolve_platform', return_value=target):
            asyncio.run(handle("вимкни комп'ютер", context, {}))
            target.system_action.assert_not_called()
            context.confirm.return_value = True
            target.system_action.return_value = True
            asyncio.run(handle("вимкни комп'ютер", context, {}))
            target.system_action.assert_called_once_with('shutdown')

    def test_doctor_linux_reports_missing_tts_not_success(self):
        from config import Settings, ProjectPaths
        from services.health.probes import tts_self_test
        with patch('platform.system', return_value='Linux'), patch(
            'services.platform.linux_tts.shutil.which', return_value=None,
        ):
            row = tts_self_test(Settings(ProjectPaths.from_root(Path.cwd())))
        self.assertEqual(row['status'], 'NOT_AVAILABLE')
        self.assertIn('RHVoice-test', row['data']['exception_message'])

    def test_linux_tts_synthesizes_and_plays_temporary_wav(self):
        from services.platform import linux_tts

        def synthesize(command, **kwargs):
            self.assertEqual(command[1:5], ['-p', 'Volodymyr', '-r', '100'])
            self.assertEqual(kwargs['input_bytes'], 'Привіт'.encode())
            Path(command[-1]).write_bytes(b'fixture')
            return True

        owner = SimpleNamespace(settings=SimpleNamespace(tts_voice_hint='Володимир', tts_rate=185, tts_volume=1),
                                _stop_requested=threading.Event(), _is_valid_wav=lambda path: path.is_file(),
                                _play_wav=Mock())
        with patch.object(linux_tts, '_executable', return_value='/usr/bin/RHVoice-test'), patch.object(
            linux_tts, '_run', side_effect=synthesize,
        ):
            PlatformServices('Linux', 'arm64').speech(owner, 'Привіт', None)
        owner._play_wav.assert_called_once()
        self.assertFalse(owner._play_wav.call_args.args[0].exists())

    def test_linux_tts_cancellation_kills_and_reaps_child(self):
        from services.platform import linux_tts

        stop = threading.Event()
        process = Mock()
        process.poll.return_value = None

        def interrupt(*args, **kwargs):
            stop.set()
            raise subprocess.TimeoutExpired('fixture', 0.05)

        process.communicate.side_effect = interrupt
        child = MagicMock()
        child.__enter__.return_value = process
        with patch.object(linux_tts.subprocess, 'Popen', return_value=child):
            self.assertFalse(linux_tts._run(['fixture'], timeout=1, stop_requested=stop))
        process.kill.assert_called_once()
        child.__exit__.assert_called_once()
