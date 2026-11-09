"""Computer control as model ACTIONs (user decision 07.11.2026).

No confirmation: media keys, show desktop, switching to a window. Correlated
confirmation: closing a window, locking the screen, a screenshot. Damaged speech
reaches none of them (tests/test_turn_pipeline_snapshot.py).
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import command_actions
from core.action_proposal import proposal_question
from core.command_catalog import TOOL_SKILLS
from core.command_intent import CommandIntent, InvalidIntent, validate_intent
from core.models import CommandContext

SKILLS = {"media", "windows", "system", "apps", "web", "files", "workplace"}


def context(confirm=True, **services):
    confirm_mock = AsyncMock(return_value=confirm)
    ctx = CommandContext(SimpleNamespace(), {"enabled_skills": SKILLS, **services}, confirm_mock, "voice")
    return ctx, confirm_mock


class ValidationTests(unittest.TestCase):
    def test_media_actions_and_steps_are_closed(self):
        ok = validate_intent({"tool": "media_control", "arguments": {"action": "volume_up", "steps": "10"}})
        self.assertEqual(ok.arguments, {"action": "volume_up", "steps": "10"})
        forced = validate_intent({"tool": "media_control", "arguments": {"action": "next_track", "steps": "7"}})
        self.assertEqual(forced.arguments["steps"], "1")  # one key press for every non-volume action
        for arguments in ({"action": "volume_up", "steps": "0"}, {"action": "volume_up", "steps": "11"},
                          {"action": "volume_up", "steps": "3; shutdown"}, {"action": "shutdown", "steps": "1"},
                          {"steps": "3"}):
            with self.subTest(arguments=arguments), self.assertRaises(InvalidIntent):
                validate_intent({"tool": "media_control", "arguments": arguments})

    def test_live_test_model_variants_of_media_arguments(self):
        # 07.11.2026: a pause without `steps`, and `steps` sent as a number.
        paused = validate_intent({"tool": "media_control", "arguments": {"action": "play_pause"}})
        self.assertEqual(paused.arguments, {"action": "play_pause", "steps": "1"})
        number = validate_intent({"tool": "media_control", "arguments": {"action": "volume_up", "steps": 5}})
        self.assertEqual(number.arguments["steps"], "5")
        for arguments in ({"action": "volume_up", "steps": True}, {"action": "volume_up", "steps": 3.5},
                          {"action": "volume_up", "steps": 12}, {"action": "volume_up", "steps": "3", "extra": "x"},
                          {"steps": "3"}):
            with self.subTest(arguments=arguments), self.assertRaises(InvalidIntent):
                validate_intent({"tool": "media_control", "arguments": arguments})
        # No other tool gains optional fields.
        with self.assertRaises(InvalidIntent):
            validate_intent({"tool": "window_control", "arguments": {"name": "Notepad"}})

    def test_window_actions_are_closed(self):
        for action in ("focus", "close"):
            validate_intent({"tool": "window_control", "arguments": {"name": "Notepad", "action": action}})
        for action in ("terminate", "kill", "delete"):
            with self.subTest(action=action), self.assertRaises(InvalidIntent):
                validate_intent({"tool": "window_control", "arguments": {"name": "Notepad", "action": action}})
        for tool in ("show_desktop", "lock_screen", "screenshot"):
            validate_intent({"tool": tool, "arguments": {}})
            with self.subTest(tool=tool), self.assertRaises(InvalidIntent):
                validate_intent({"tool": tool, "arguments": {"path": "C:/x"}})

    def test_every_model_tool_has_its_own_handler(self):
        # A tool without a handler must never fall through to another tool (it used to reach web search).
        self.assertEqual(set(TOOL_SKILLS), set(command_actions._HANDLERS))

    def test_proposal_wording_covers_new_window_actions(self):
        self.assertEqual(proposal_question(CommandIntent("window_control", {"name": "Notepad", "action": "close"})),
                         "Закрити вікно «Notepad»?")
        self.assertEqual(proposal_question(CommandIntent("window_control", {"name": "Telegram", "action": "focus"})),
                         "Перейти до вікна «Telegram»?")


# pyautogui exists only on Windows (requirements_win.txt); macOS and Linux have their own paths and tests.
WINDOWS_ONLY = unittest.skipUnless(importlib.util.find_spec('pyautogui'), 'pyautogui is Windows-only')


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    @WINDOWS_ONLY
    async def test_media_key_runs_without_confirmation_and_says_only_sent(self):
        ctx, confirm = context()
        with patch("skills.media.skill.pyautogui.press") as press:
            result = await command_actions.execute_intent(
                CommandIntent("media_control", {"action": "volume_down", "steps": "4"}), ctx)
        press.assert_called_once_with("volumedown", presses=4)
        confirm.assert_not_awaited()
        self.assertIn("Надіслав", result.response)
        self.assertFalse(result.data["verified"])

    @WINDOWS_ONLY
    async def test_show_desktop_runs_without_confirmation(self):
        ctx, confirm = context()
        with patch("pyautogui.hotkey") as hotkey:
            result = await command_actions.execute_intent(CommandIntent("show_desktop", {}), ctx)
        hotkey.assert_called_once_with("win", "d")
        confirm.assert_not_awaited()
        self.assertEqual(result.data["status"], "sent")

    async def test_lock_screen_needs_confirmation(self):
        for accepted in (False, True):
            ctx, confirm = context(confirm=accepted)
            platform = SimpleNamespace(system_action=Mock(return_value=True))
            with self.subTest(accepted=accepted), patch("skills.system.skill.resolve_platform", return_value=platform):
                result = await command_actions.execute_intent(CommandIntent("lock_screen", {}), ctx)
                confirm.assert_awaited_once()
                self.assertEqual(platform.system_action.called, accepted)
                self.assertIn("заблоковано" if accepted else "скасовано", result.response)

    @WINDOWS_ONLY
    async def test_screenshot_needs_confirmation_and_stays_local(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "Raven"

            def fake_shot(path):
                Path(path).write_bytes(b"png")

            for accepted in (False, True):
                ctx, confirm = context(confirm=accepted)
                with self.subTest(accepted=accepted), \
                        patch("skills.system.skill.screenshot_folder", return_value=folder), \
                        patch("pyautogui.screenshot", side_effect=fake_shot) as shot:
                    result = await command_actions.execute_intent(CommandIntent("screenshot", {}), ctx)
                    confirm.assert_awaited_once()
                    self.assertEqual(shot.called, accepted)
                    if accepted:
                        self.assertTrue(result.data["verified"])
                        self.assertEqual(len(list(folder.glob("screenshot-*.png"))), 1)
                    else:
                        self.assertIn("скасовано", result.response)

    def _windows(self):
        target = {"hwnd": 1, "pid": 2, "created": 3.0, "exe": "C:/Windows/notepad.exe", "name": "Блокнот"}
        controller = SimpleNamespace(candidates=Mock(return_value=[target]),
                                     change_target=Mock(return_value={"accepted": True, "verified": True,
                                                                      "status": "verified", "foreground": True}))
        return controller

    async def test_focus_runs_without_confirmation_close_needs_it(self):
        for action, accepted, asked, changed in (("focus", False, False, True), ("close", False, True, False),
                                                 ("close", True, True, True)):
            controller = self._windows()
            ctx, confirm = context(confirm=accepted, windows=controller)
            with self.subTest(action=action, accepted=accepted):
                result = await command_actions.execute_intent(
                    CommandIntent("window_control", {"name": "Блокнот", "action": action}), ctx)
                self.assertEqual(confirm.await_count > 0, asked)
                self.assertEqual(controller.change_target.called, changed)
                if changed:
                    self.assertEqual(controller.change_target.call_args.args[1], action)
                    self.assertIn("Перейшов" if action == "focus" else "закрито", result.response)

    async def test_close_that_program_refuses_is_reported_honestly(self):
        controller = self._windows()
        controller.change_target.return_value = {"accepted": True, "verified": False, "status": "unverified"}
        ctx, _confirm = context(windows=controller)
        result = await command_actions.execute_intent(
            CommandIntent("window_control", {"name": "Блокнот", "action": "close"}), ctx)
        self.assertIn("питати про збереження", result.response)
        self.assertFalse(result.data["success"])

    async def test_tray_program_is_reported_as_hidden_not_as_a_save_prompt(self):
        controller = self._windows()
        controller.change_target.return_value = {"accepted": True, "verified": True, "status": "verified",
                                                 "hidden": True}
        ctx, _confirm = context(windows=controller)
        result = await command_actions.execute_intent(
            CommandIntent("window_control", {"name": "Блокнот", "action": "close"}), ctx)
        self.assertIn("сховано", result.response)
        self.assertIn("треї", result.response)

    @WINDOWS_ONLY
    async def test_disabled_skill_is_refused(self):
        ctx = CommandContext(SimpleNamespace(), {"enabled_skills": {"web"}}, AsyncMock(return_value=True), "voice")
        with patch("skills.media.skill.pyautogui.press") as press:
            result = await command_actions.execute_intent(
                CommandIntent("media_control", {"action": "mute_toggle", "steps": "1"}), ctx)
        press.assert_not_called()
        self.assertIn("вимкнено", result.response)


class WindowObservationTests(unittest.TestCase):
    def test_close_is_verified_only_when_the_window_is_gone(self):
        from services.windows.window_controller import WindowController
        gone = SimpleNamespace(IsWindow=lambda hwnd: False, IsWindowVisible=lambda hwnd: False)
        with patch.dict("sys.modules", {"win32gui": gone}):
            self.assertTrue(WindowController._observed(SimpleNamespace(), "close", 7))
            self.assertEqual(WindowController._tray_hint("close", True, 7), {"hidden": False})
        visible = SimpleNamespace(IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: True)
        with patch.dict("sys.modules", {"win32gui": visible}):
            self.assertFalse(WindowController._observed(SimpleNamespace(), "close", 7))
            self.assertEqual(WindowController._tray_hint("close", False, 7), {})
        hidden = SimpleNamespace(IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: False)
        with patch.dict("sys.modules", {"win32gui": hidden}):  # tray program: the window only hides
            self.assertTrue(WindowController._observed(SimpleNamespace(), "close", 7))
            self.assertEqual(WindowController._tray_hint("close", True, 7), {"hidden": True})
            self.assertEqual(WindowController._tray_hint("focus", True, 7), {})

    def test_focus_restores_a_minimized_window_then_activates(self):
        from services.windows.window_controller import WindowController
        window = SimpleNamespace(isMinimized=True, restore=Mock(), activate=Mock(), close=Mock())
        WindowController._apply(window, "focus")
        window.restore.assert_called_once()
        window.activate.assert_called_once()
        window.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
