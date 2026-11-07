from __future__ import annotations

import asyncio

import pyautogui

from core.models import SkillResult

# Model ACTION media_control (user decision 07.11.2026): fixed media keys only, no
# confirmation, as reversible as a key on the keyboard. Windows reports nothing back,
# so the reply says the key was sent, never that the volume changed.
MEDIA_KEYS = {"volume_up": "volumeup", "volume_down": "volumedown", "mute_toggle": "volumemute",
              "play_pause": "playpause", "next_track": "nexttrack", "previous_track": "prevtrack"}
_SENT = {"volume_up": "гучніше на {n}", "volume_down": "тихіше на {n}", "mute_toggle": "вимкнути або увімкнути звук",
         "play_pause": "пауза або відтворення", "next_track": "наступний трек", "previous_track": "попередній трек"}


async def press_media(action, steps):
    """One validated media key; `steps` presses only for volume (validated 1–10)."""
    presses = int(steps) if action in {"volume_up", "volume_down"} else 1
    await asyncio.to_thread(pyautogui.press, MEDIA_KEYS[action], presses=presses)
    return SkillResult(True, f"Надіслав медіакоманду: {_SENT[action].format(n=presses)}.",
                       {"command_type": "media", "accepted": True, "verified": False, "status": "sent"})


def can_handle(command, services):
    return command in {
        "збільш гучність",
        "зменш гучність",
        "вимкни звук",
        "увімкни звук",
        "пауза",
        "продовж відтворення",
        "наступний трек",
        "попередній трек",
    }


async def handle(command, context, services):
    if "збільш" in command and "гуч" in command:
        pyautogui.press("volumeup", presses=3)
        response = "Гучність збільшено."
    elif "зменш" in command and "гуч" in command:
        pyautogui.press("volumedown", presses=3)
        response = "Гучність зменшено."
    elif command in {"вимкни звук", "увімкни звук"}:
        pyautogui.press("volumemute")
        response = "Звук перемкнено."
    elif "наступн" in command:
        pyautogui.press("nexttrack")
        response = "Наступний трек."
    elif "попередн" in command:
        pyautogui.press("prevtrack")
        response = "Попередній трек."
    else:
        pyautogui.press("playpause")
        response = "Відтворення перемкнено."
    return SkillResult(True, response, {"command_type": "media"})
