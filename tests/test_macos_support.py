"""macOS support: offline, mocked. Nothing here touches a real Mac, microphone or the OS.

These tests prove argv construction, error mapping, platform gating and Windows
non-regression. They do NOT prove behaviour on real macOS (docs/MACOS_SETUP.md).
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.platform import PlatformOperationError, macos, macos_permissions
from services.platform.resolver import PlatformCapabilityUnavailable, PlatformServices, manifest_platform_matches

ROOT = Path(__file__).resolve().parents[1]
DARWIN = PlatformServices("Darwin", "arm64")
UNAVAILABLE_ON_MAC = {"windows", "workplace"}


def completed(code=0, stderr=b"", stdout=b""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


class ResolverTests(unittest.TestCase):
    def test_darwin_capabilities_are_explicit(self):
        report = DARWIN.report()["capabilities"]
        for name in ("tts", "application_launch", "file_open", "session_control", "media_control", "screenshot"):
            self.assertEqual(report[name], "IMPLEMENTED", name)
        for name in ("window_control", "process_control", "power_control", "workplace", "drive_inventory"):
            self.assertEqual(report[name], "NOT_IMPLEMENTED", name)

    def test_power_and_window_control_are_unsupported_on_mac(self):
        with self.assertRaises(PlatformCapabilityUnavailable):
            DARWIN.system_action("shutdown")
        with self.assertRaises(PlatformCapabilityUnavailable):
            DARWIN.windows().candidates("x")

    def test_resolve_platform_recognizes_darwin(self):
        from services.platform.resolver import resolve_platform
        with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"):
            found = resolve_platform()
        self.assertEqual((found.os, found.architecture), ("Darwin", "arm64"))

    def test_manifest_platform_matching(self):
        cases = {"Darwin": {("darwin",): True, ("macos",): True, ("windows",): False, ("all",): True,
                            ("windows", "linux"): False},
                 "Windows": {("windows",): True, ("darwin",): False, ("all",): True},
                 "Linux": {("linux",): True, ("darwin",): False}}
        for system, expectations in cases.items():
            with patch("platform.system", return_value=system):
                for platforms, expected in expectations.items():
                    self.assertEqual(manifest_platform_matches(list(platforms)), expected, (system, platforms))

    def test_windows_capabilities_unchanged(self):
        windows = PlatformServices("Windows", "x86_64")
        for name in ("tts", "window_control", "power_control", "workplace", "session_control"):
            self.assertTrue(windows.supports(name), name)


class ManifestTests(unittest.TestCase):
    def manifests(self):
        return {path.parent.name: json.loads(path.read_text(encoding="utf-8"))
                for path in (ROOT / "skills").glob("*/manifest.json")}

    def test_windows_only_skills_stay_off_on_mac_and_others_stay_on(self):
        with patch("platform.system", return_value="Darwin"):
            enabled = {name for name, manifest in self.manifests().items()
                       if manifest_platform_matches(manifest["platforms"])}
        for name in UNAVAILABLE_ON_MAC:
            self.assertNotIn(name, enabled)
        for name in ("media", "system", "apps", "files", "notes", "memory", "reminders", "web", "help"):
            self.assertIn(name, enabled)

    def test_windows_set_is_unchanged(self):
        with patch("platform.system", return_value="Windows"):
            enabled = {name for name, manifest in self.manifests().items()
                       if manifest_platform_matches(manifest["platforms"])}
        self.assertEqual(enabled, set(self.manifests()))

    def test_skills_load_on_mac_without_windows_only_packages(self):
        blocked = {name: None for name in ("pyautogui", "pygetwindow", "win32gui", "win32process", "win32com")}
        for name in ("media", "system", "apps"):
            path = ROOT / "skills" / name / "skill.py"
            spec = importlib.util.spec_from_file_location(f"_mac_probe_{name}", path)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, blocked):
                spec.loader.exec_module(module)  # must not raise ImportError


class OpenTests(unittest.TestCase):
    def run_open(self, command, which=None):
        with patch("subprocess.run", return_value=completed()) as run, \
                patch("shutil.which", return_value=which):
            self.assertTrue(macos.open_application({"command": command}))
        return run.call_args.args[0]

    def test_app_bundle_opens_with_open(self):
        self.assertEqual(self.run_open("/Applications/Safari.app"), ["open", "/Applications/Safari.app"])

    def test_plain_name_opens_with_open_dash_a(self):
        self.assertEqual(self.run_open("Visual Studio Code"), ["open", "-a", "Visual Studio Code"])

    def test_executable_on_path_is_started_directly_without_shell(self):
        with patch("shutil.which", return_value="/usr/bin/true"), \
                patch("subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.assertTrue(macos.open_application({"command": "true --flag"}))
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["/usr/bin/true", "--flag"])
        self.assertIs(popen.call_args.kwargs["shell"], False)

    def test_open_failure_is_a_fixed_error(self):
        with patch("subprocess.run", return_value=completed(1)), patch("shutil.which", return_value=None):
            with self.assertRaises(PlatformOperationError) as caught:
                macos.open_application({"command": "Nope"})
        self.assertEqual(caught.exception.status, "execution_failed")

    def test_missing_open_binary_is_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError), patch("shutil.which", return_value=None):
            with self.assertRaises(PlatformOperationError) as caught:
                macos.open_file("/tmp/x.txt")
        self.assertEqual(caught.exception.status, "not_found")

    def test_file_name_cannot_become_an_option(self):
        with patch("subprocess.run", return_value=completed()) as run:
            macos.open_file("-a")
        self.assertEqual(run.call_args.args[0], ["open", "./-a"])


class SessionAndMediaTests(unittest.TestCase):
    def test_lock_sends_ctrl_cmd_q(self):
        with patch("subprocess.run", return_value=completed()) as run:
            self.assertTrue(macos.system_action("lock"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "osascript")
        self.assertIn("control down, command down", " ".join(argv))

    def test_accessibility_denial_maps_to_permission_denied(self):
        stderr = b"osascript is not allowed assistive access. (-25211)"
        with patch("subprocess.run", return_value=completed(1, stderr)):
            with self.assertRaises(PlatformOperationError) as caught:
                macos.system_action("lock")
        self.assertEqual((caught.exception.capability, caught.exception.status),
                         ("session_control", "permission_denied"))

    def test_shutdown_is_never_run(self):
        with patch("subprocess.run") as run, self.assertRaises(PlatformCapabilityUnavailable):
            macos.system_action("shutdown")
        run.assert_not_called()

    def test_volume_uses_clamped_osascript(self):
        with patch("subprocess.run", return_value=completed()) as run:
            macos.media_key("volume_up", 2)
            macos.media_key("volume_down", 1)
        up, down = (" ".join(call.args[0]) for call in run.call_args_list)
        self.assertIn("+ (12)", up)
        self.assertIn("+ (-6)", down)
        self.assertIn("set volume output volume v", up)

    def test_mute_toggle_and_transport_keys(self):
        with patch("subprocess.run", return_value=completed()) as run:
            macos.media_key("mute_toggle")
        self.assertIn("output muted", " ".join(run.call_args.args[0]))
        with patch.object(macos, "_post_transport_key") as post:
            macos.media_key("play_pause")
            macos.media_key("next_track")
            macos.media_key("previous_track")
        self.assertEqual([call.args[0] for call in post.call_args_list], [16, 17, 18])

    def test_transport_key_without_pyobjc_is_not_available(self):
        with patch.dict(sys.modules, {"AppKit": None, "Quartz": None}):
            with self.assertRaises(PlatformOperationError) as caught:
                macos.media_key("play_pause")
        self.assertEqual(caught.exception.status, "not_available")

    def test_unknown_media_action_is_unsupported(self):
        with self.assertRaises(PlatformCapabilityUnavailable):
            macos.media_key("eject")

    def test_screenshot_refused_when_screen_recording_is_denied(self):
        with patch.object(macos_permissions, "screen_recording_status", return_value="denied"), \
                patch("subprocess.run") as run:
            with self.assertRaises(PlatformOperationError) as caught:
                macos.take_screenshot(Path("x.png"))
        self.assertEqual(caught.exception.status, "permission_denied")
        run.assert_not_called()

    def test_screenshot_runs_screencapture_and_checks_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shot.png"

            def fake_run(argv, **kwargs):
                Path(argv[-1]).write_bytes(b"png")
                return completed()
            with patch.object(macos_permissions, "screen_recording_status", return_value="granted"), \
                    patch("subprocess.run", side_effect=fake_run) as run:
                self.assertTrue(macos.take_screenshot(path))
        self.assertEqual(run.call_args.args[0][:3], ["screencapture", "-x", "-t"])


VOICES = (b"Alex                en_US    # Most people recognize me by my voice.\n"
          b"Eddy (English (UK)) en_GB    # Hello!\n"
          b"Lesya               uk_UA    # Hello, my name is Lesya.\n")


class SpeechTests(unittest.TestCase):
    def setUp(self):
        macos.resolve_voice.cache_clear()
        self.addCleanup(macos.resolve_voice.cache_clear)

    def voices(self, data=VOICES):
        return patch("subprocess.run", return_value=completed(stdout=data))

    def test_hint_is_matched_before_the_ukrainian_fallback(self):
        with self.voices():
            self.assertEqual(macos.resolve_voice("alex"), ("Alex", "en_US", False))

    def test_ukrainian_voice_is_the_fallback_for_a_windows_hint(self):
        with self.voices():
            self.assertEqual(macos.resolve_voice("Volodymyr"), ("Lesya", "uk_UA", True))

    def test_no_voices_means_system_default(self):
        with self.voices(b""):
            self.assertEqual(macos.resolve_voice(""), (None, None, False))

    def test_say_reads_text_from_stdin_never_from_argv(self):
        argv = macos._say_argv("Lesya", 185, Path("out.wav"))
        self.assertEqual(argv[-2:], ["-f", "-"])
        self.assertIn("--file-format=WAVE", argv)
        self.assertEqual(argv[argv.index("-r") + 1], "185")
        self.assertEqual(macos._say_argv(None, 9999)[macos._say_argv(None, 9999).index("-r") + 1], "400")

    def owner(self):
        return SimpleNamespace(settings=SimpleNamespace(tts_voice_hint="Lesya", tts_rate=185),
                               _lock=threading.RLock(), _process_lock=threading.Lock(),
                               _stop_requested=threading.Event(), _closing=False, _tts_process=None,
                               _set_playback=MagicMock(), _current_timing=None, _stop_sync=MagicMock())

    def fake_process(self, code=0):
        process = MagicMock(returncode=code)
        process.poll.return_value = code
        return process

    def test_speech_streams_text_to_say_and_tracks_the_process(self):
        owner = self.owner()
        process = self.fake_process()
        with self.voices(), patch("subprocess.Popen", return_value=process) as popen:
            macos.speech(owner, "Привіт", None)
        process.stdin.write.assert_called_once_with("Привіт".encode("utf-8"))
        process.stdin.close.assert_called()
        self.assertIs(owner._tts_process, process)
        owner._set_playback.assert_called_with(True)
        self.assertNotIn("Привіт", popen.call_args.args[0])

    def test_empty_warmup_text_starts_nothing(self):
        with patch("subprocess.Popen") as popen:
            macos.speech(self.owner(), "  ", None)
        popen.assert_not_called()

    def test_say_failure_raises(self):
        with self.voices(), patch("subprocess.Popen", return_value=self.fake_process(1)):
            with self.assertRaises(RuntimeError):
                macos.speech(self.owner(), "Привіт", Path("x.wav"))

    def test_stop_request_before_start_speaks_nothing(self):
        owner = self.owner()
        owner._stop_requested.set()
        with self.voices(), patch("subprocess.Popen") as popen:
            macos.speech(owner, "Привіт", None)
        popen.assert_not_called()

    def test_probe_reports_fallback_voice(self):
        with self.voices(), patch("subprocess.run", return_value=completed(stdout=VOICES)):
            result = macos.synthesize_probe(Path("p.wav"), "Volodymyr")
        self.assertEqual((result["status"], result["voice"], result["fallback"]), ("ok", "Lesya", True))

    def test_play_wav_and_stop_playback(self):
        process = MagicMock()
        process.poll.return_value = None
        with patch("subprocess.Popen", return_value=process) as popen:
            macos.play_wav(Path("a.wav"))
            macos.stop_playback()
        self.assertEqual(popen.call_args.args[0], ["afplay", "a.wav"])
        process.terminate.assert_called_once()


class SkillOnMacTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_uses_platform_not_pyautogui(self):
        from skills.media import skill
        fake = MagicMock(os="Darwin")
        with patch.object(skill, "resolve_platform", return_value=fake), \
                patch.object(skill, "pyautogui", None):
            result = await skill.press_media("volume_up", 3)
        fake.media_key.assert_called_once_with("volume_up", 3)
        self.assertTrue(result.handled)
        self.assertEqual(result.data["status"], "sent")
        self.assertFalse(result.data["verified"])

    async def test_media_permission_error_is_reported_not_raised(self):
        from skills.media import skill
        fake = MagicMock(os="Darwin")
        fake.media_key.side_effect = PlatformOperationError("media_control", "permission_denied")
        with patch.object(skill, "resolve_platform", return_value=fake):
            result = await skill.press_media("play_pause", 1)
        self.assertFalse(result.data["success"])
        self.assertIn("не надіслано", result.response)

    async def test_lock_permission_error_says_not_locked(self):
        from skills.system import skill
        fake = MagicMock(os="Darwin")
        fake.system_action.side_effect = PlatformOperationError("session_control", "permission_denied")
        with patch.object(skill, "resolve_platform", return_value=fake), \
                patch("core.confirmation.confirm_action", AsyncMock(return_value=None)):
            result = await skill.lock_screen(SimpleNamespace())
        self.assertIn("не заблоковано", result.response)
        self.assertFalse(result.data["verified"])

    async def test_screenshot_without_permission_is_honest(self):
        from skills.system import skill
        fake = MagicMock(os="Darwin")
        fake.take_screenshot.side_effect = PlatformOperationError("screenshot", "permission_denied")
        with patch.object(skill, "resolve_platform", return_value=fake), \
                patch("core.confirmation.confirm_action", AsyncMock(return_value=None)), \
                patch.object(skill, "screenshot_folder", return_value=Path(tempfile.gettempdir()) / "raven-x"):
            result = await skill.save_screenshot(SimpleNamespace())
        self.assertIn("не збережено", result.response)
        self.assertFalse(result.data["success"])


class IndexerTests(unittest.TestCase):
    def test_app_bundles_are_indexed_not_executed(self):
        from services.apps.indexer import macos_applications
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "Safari.app").mkdir()
            (Path(folder) / "notes.txt").write_text("x")
            found = macos_applications((folder, str(Path(folder) / "missing")))
        self.assertEqual(list(found), ["safari"])
        self.assertTrue(found["safari"]["command"].endswith("Safari.app"))


class PermissionTests(unittest.TestCase):
    def test_unknown_when_frameworks_are_missing(self):
        with patch.dict(sys.modules, {"ApplicationServices": None, "Quartz": None, "AVFoundation": None}):
            self.assertEqual(set(macos_permissions.report().values()), {"unknown"})

    def test_statuses_from_frameworks(self):
        fake = {"ApplicationServices": SimpleNamespace(AXIsProcessTrusted=lambda: True),
                "Quartz": SimpleNamespace(CGPreflightScreenCaptureAccess=lambda: False),
                "AVFoundation": SimpleNamespace(AVCaptureDevice=SimpleNamespace(
                    authorizationStatusForMediaType_=lambda kind: 0 if kind == "soun" else 3))}
        with patch.dict(sys.modules, fake):
            self.assertEqual(macos_permissions.report(), {"microphone": "not_determined",
                                                          "accessibility": "granted",
                                                          "screen_recording": "denied"})


class CheckToolTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("_macos_check", ROOT / "tools" / "macos_check.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_collect_reports_missing_tools_and_missing_ukrainian_voice(self):
        module = self.load()
        run = MagicMock(return_value=completed(stdout=b"Alex en_US # hi\n"))
        with patch.object(module, "_audio_input", return_value=("OK", "мікрофон", "x")), \
                patch.object(module, "_permissions", return_value=[]):
            checks = module.collect(which=lambda name: None if name == "afplay" else "/usr/bin/" + name, run=run)
        levels = {name: level for level, name, _ in checks}
        self.assertEqual(levels["утиліта afplay"], "FAIL")
        self.assertEqual(levels["утиліта say"], "OK")
        self.assertEqual(levels["український голос"], "WARN")


class DependencyAndScriptTests(unittest.TestCase):
    def requirement_class(self):
        try:
            from packaging.requirements import Requirement
        except ImportError:
            from pip._vendor.packaging.requirements import Requirement
        return Requirement

    def lines(self, name):
        return [line.strip() for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith(("#", "-"))]

    def applies(self, requirement, system, machine="x86_64"):
        env = {"sys_platform": {"Darwin": "darwin", "Windows": "win32"}[system], "platform_system": system,
               "platform_machine": machine}
        return requirement.marker is None or requirement.marker.evaluate(env)

    def test_vosk_uses_a_version_with_a_macos_wheel_only_on_mac(self):
        Requirement = self.requirement_class()

        def vosk(file_name):
            return [str(Requirement(line).specifier) for line in self.lines(file_name) if line.startswith("vosk")]

        self.assertEqual(vosk("requirements_mac.txt"), ["==0.3.44"])
        self.assertEqual(vosk("requirements_win.txt"), [">=0.3.45"])
        self.assertEqual(vosk("requirements_linux.txt"), [">=0.3.45"])

    def test_windows_only_packages_never_apply_on_mac(self):
        Requirement = self.requirement_class()
        windows_only = {"pywin32", "pygetwindow", "pyautogui", "pyttsx3"}
        for file_name in ("requirements_mac.txt", "requirements_linux.txt"):
            names = {Requirement(line).name.lower() for line in self.lines(file_name)}
            self.assertFalse(names & windows_only, file_name)
        names = {Requirement(line).name.lower() for line in self.lines("requirements_win.txt")}
        self.assertTrue(windows_only <= names)
        for name in ("requirements_mac.txt", "requirements_linux.txt", "requirements_win.txt"):
            self.assertIn("groq", {Requirement(line).name.lower() for line in self.lines(name)}, name)

    def test_macos_requirements_have_pyobjc_and_intel_cryptography_cap(self):
        Requirement = self.requirement_class()
        parsed = [Requirement(line) for line in self.lines("requirements_mac.txt")]
        names = {r.name.lower() for r in parsed}
        self.assertTrue({"pyobjc-framework-quartz", "pyobjc-framework-cocoa",
                         "pyobjc-framework-applicationservices", "pyobjc-framework-avfoundation"} <= names)
        crypto = [r for r in parsed if r.name == "cryptography"][0]
        self.assertTrue(self.applies(crypto, "Darwin", "x86_64"))
        self.assertFalse(self.applies(crypto, "Darwin", "arm64"))
        windows = {Requirement(line).name.lower() for line in self.lines("requirements_win.txt")}
        self.assertFalse({name for name in names if name.startswith("pyobjc")} & windows)

    def test_install_script_is_safe(self):
        text = (ROOT / "scripts" / "install_macos.sh").read_text(encoding="utf-8")
        self.assertNotIn("\r", text)
        code = chr(10).join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("sudo ", code.replace("без sudo", "").replace("не використовує sudo", ""))
        self.assertNotIn("git add", text)
        self.assertNotIn("API_KEY=", text)
        self.assertNotIn("read -s", text)  # never asks for or stores a secret
        self.assertIn("if [ -f config.json ]", text)
        self.assertIn("cp config.example.json config.json", text)
        self.assertNotIn("cp -f", text)
        self.assertIn("requirements_mac.txt", text)
        self.assertLess(text.index("if [ -f config.json ]"), text.index("cp config.example.json config.json"))

    def test_scripts_have_valid_bash_syntax(self):
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash недоступний")
        for name in ("install_macos.sh", "run_macos.sh"):
            result = subprocess.run([bash, "-n", str(ROOT / "scripts" / name)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_run_script_requires_venv_and_forwards_arguments(self):
        text = (ROOT / "scripts" / "run_macos.sh").read_text(encoding="utf-8")
        self.assertIn('exec .venv/bin/python main.py "$@"', text)
        self.assertIn(".venv/bin/python", text)

    def test_setup_guide_is_linked_from_main_docs(self):
        for name in ("README.md", "docs/USER_GUIDE.md", "docs/CONFIGURATION.md"):
            self.assertIn("MACOS_SETUP.md", (ROOT / name).read_text(encoding="utf-8"), name)
        guide = (ROOT / "docs" / "MACOS_SETUP.md").read_text(encoding="utf-8")
        self.assertIn("НЕ ПЕРЕВІРЕНО на реальному Mac", guide)


if __name__ == "__main__":
    unittest.main()
