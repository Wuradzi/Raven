"""In-process boundary between the desktop window and Core: events out, validated actions in.

No server, no network and no tool bypass: messages go to the existing command queue and every
other action to RavenApp.web_control, which owns confirmation, cancellation and settings.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

from core.security import redact_user_text

logger = logging.getLogger(__name__)
CONTROL_ACTIONS = frozenset({"stop", "pause", "resume", "microphone", "confirm", "cancel_task", "cancel_search",
                             "cancel_progress", "stt_backend"})


class UIBridge:
    source_label = 'UI'

    def __init__(self, app):
        self.app = app
        self.events = deque(maxlen=300)
        self.sequence = 0
        self.changed = asyncio.Event()
        self.submission = None

    def publish(self, kind, text="", **data):
        self.sequence += 1
        self.events.append({"id": self.sequence, "kind": kind,
                            "text": redact_user_text(str(text))[:12000], **data})
        self.changed.set()

    async def close(self):
        if self.submission and not self.submission.done():
            self.submission.cancel()
            await asyncio.gather(self.submission, return_exceptions=True)

    async def _submit(self, text, confirmation_id=None):
        try:
            logging.getLogger("session.dialogue").info("[%s] %s", self.source_label, redact_user_text(text))
            await self.app._submit_text(text, confirmation_id)
        except Exception:
            logger.exception("UI submission failed")
            self.publish("notice", "Повідомлення не вдалося передати. Перевірте журнал сесії.")

    async def handle_action(self, data):
        """(status, body) for one window action; validation mirrors the former local API."""
        if not isinstance(data, dict):
            return 400, {"error": "Очікується об’єкт."}
        action = data.get("action")
        if action in CONTROL_ACTIONS:
            return await self.app.web_control(action, data)
        if action != "message":
            return 400, {"error": "Невідома дія."}
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            return 400, {"error": "Повідомлення має містити від 1 до 4000 символів."}
        if self.submission and not self.submission.done():
            return 409, {"error": "Попереднє повідомлення ще передається. Зачекайте."}
        request_id = data.get("request_id")
        if request_id is not None and not isinstance(request_id, str):
            return 400, {"error": "Некоректний ID підтвердження."}
        # The existing router, confirmation service and command queue own all actions.
        self.submission = asyncio.create_task(self._submit(text.strip(), request_id), name="ui-submit")
        return 202, {"ok": True}
