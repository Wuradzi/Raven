"""Compatibility policy for recognition evidence; no audio devices or execution.

Rules are extracted verbatim from the listener. A refinement hint is not an
execution permission. Pending clarification belongs to DialogueState.
"""
from __future__ import annotations

import re
import time
from typing import TypedDict

from core.models import RecognitionResult
from core.action_proposal import ProposalState
from core.natural_turn import (
    direct_request, voice_request, short_clarification, application_name_reply,
)


class TurnRecognitionFlags(TypedDict):
    """Queue-boundary projection only; does not infer or grant execution trust."""
    confidence_available: bool
    utterance_incomplete: bool
    capture_truncated: bool
    recognition_unreliable: bool
    action_eligible: bool
    clarification_required: bool


class DialogueState:
    """Existing single pending clarification; approval state stays elsewhere."""

    def __init__(self, services=None):
        self.pending = None
        self.proposals = ProposalState()
        self.services = services if services is not None else {}

    def scoped_reply(self, text):
        pending = self.pending
        if not (pending and time.monotonic() < pending['expires']
                and pending.get('tool') in {'open_app', 'window_control'}
                and short_clarification(text) and not direct_request(voice_request(text), '')):
            return False
        name = application_name_reply(text)
        exact = getattr(self.services.get('apps'), 'has_exact_name', None)
        return bool(name and callable(exact) and exact(name))


class RecognitionPolicy:
    FAST_CHAT = re.compile(
        r"^(?:рейвен[\s,]+)?(?:привіт|здорово|дякую|дякую тобі|як справи|"
        r"як твої справи|так|ні|добре|гаразд|зрозуміло|до побачення)[.!?]*$",
        re.IGNORECASE,
    )

    def __init__(self, settings, dialogue=None):
        self.settings = settings
        self.dialogue = dialogue if dialogue is not None else DialogueState()

    @staticmethod
    def action_eligible(result):
        """Legacy evidence adapter, not approval to execute."""
        return result.confidence_available and not result.fragmented

    @staticmethod
    def groq_trusted_flags(result, flags):
        """Рішення 12.11.2026: чистий текст Groq без confidence має право на дію, як оцінений STT.

        confidence_available лишається False (оцінку не вигадуємо); змінюється лише дозвіл увійти в
        політику дій. Пошкоджене, обрізане або незавершене мовлення право не отримує.
        """
        # Для Groq `fragmented` завжди True як плейсхолдер відсутнього confidence (stt_listener), тож
        # про шкоду кажуть лише явні прапорці: ненадійне, обрізане, незавершене, потрібне повторення.
        clean = (result.engine == 'groq' and not result.confidence_available
                 and not flags['recognition_unreliable'] and not flags['capture_truncated']
                 and not flags['utterance_incomplete'] and not flags['clarification_required'])
        return {**flags, 'action_eligible': True, 'cloud_trusted': True} if clean else flags

    @classmethod
    def turn_flags(cls, result: RecognitionResult) -> TurnRecognitionFlags:
        """Preserve existing queue semantics, keeping evidence axes independent.

        Do not derive eligibility from any single reliability flag here. Producers
        retain their legacy restriction and downstream policy retains its checks.
        """
        return {
            'confidence_available': result.confidence_available,
            'utterance_incomplete': result.resolved_utterance_incomplete,
            'capture_truncated': result.capture_truncated,
            'recognition_unreliable': result.recognition_unreliable,
            'action_eligible': cls.action_eligible(result),
            'clarification_required': result.incomplete,
        }

    @staticmethod
    def _contains_command_prefix(text: str) -> bool:
        normalized = " ".join(text.lower().strip().split())
        return bool(re.match(r"^команда\b", normalized))
