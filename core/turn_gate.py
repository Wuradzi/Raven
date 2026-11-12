"""One place that turns STT trust flags into routing permissions.

Read-only view of a TurnEnvelope plus the caller's flags. ALLOW / permitted
here means only "may enter the existing executor or confirmation path";
it is never authorization to run a tool (see core.action_policy).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.action_policy import PolicyOutcome, turn_permission
from core.models import TurnEnvelope


class TrustLevel(Enum):
    """How far the recognised text may be trusted (roadmap phase 1, step 1).

    A label for routing and logs, never a permission: ACTION eligibility, policy and
    confirmation still decide. Groq text without a confidence score is never SCORED.
    """
    SCORED = "scored"
    """Typed text, or speech with a known confidence; not damaged (may still be not eligible)."""
    UNSCORED_CLEAN = "unscored_clean"
    """Groq speech without a confidence score that is not damaged."""
    DAMAGED = "damaged"
    """Unreliable, truncated, cut off or a repeat requested."""


@dataclass(frozen=True, slots=True)
class TurnGate:
    caller_allows: bool
    """The caller (app loop, probe, test) did not forbid actions for this turn."""
    allow_actions: bool
    """Caller allows, STT marked the turn eligible and a confidence score exists."""
    incomplete: bool
    """Caller flag or the envelope asks for a repeat instead of a tool."""
    reliable: bool
    """Not incomplete, not truncated, not unreliable, not linguistically cut off."""
    confidence_known: bool
    groq_unscored: bool
    """Groq text has no confidence score; never treated as a measured score."""
    voice: bool
    unscored: bool = False
    """An envelope without a confidence score (any engine); False for direct string callers."""
    lexical_fragment: bool = False
    """Unscored text that is only linguistically cut off: not truncated, not unreliable."""

    @classmethod
    def evaluate(cls, turn: TurnEnvelope | None, allow_actions: bool,
                 incomplete_input: bool) -> "TurnGate":
        if turn is None:
            # Direct string callers (probes/tests) keep their existing API.
            return cls(allow_actions, allow_actions, incomplete_input, not incomplete_input,
                       True, False, False)
        incomplete = incomplete_input or turn.clarification_required
        damaged = turn.recognition_unreliable or turn.capture_truncated or turn.utterance_incomplete
        # Чистий Groq, якому STT-шар явно дав action_eligible (stt.groq_trusted): як оцінений STT.
        trusted_groq = (turn.stt_engine == "groq" and turn.cloud_trusted and turn.action_eligible
                        and not turn.confidence_available)
        return cls(
            caller_allows=allow_actions,
            allow_actions=allow_actions and turn.action_eligible and (turn.confidence_available or trusted_groq),
            incomplete=incomplete,
            reliable=not incomplete and not damaged,
            confidence_known=turn.confidence_available or trusted_groq,
            groq_unscored=turn.stt_engine == "groq" and not turn.confidence_available and not trusted_groq,
            voice=turn.source == "voice",
            unscored=not turn.confidence_available,
            lexical_fragment=(not turn.confidence_available and turn.utterance_incomplete
                              and not turn.capture_truncated and not turn.recognition_unreliable),
        )

    @property
    def trust(self) -> TrustLevel:
        if not self.reliable:
            return TrustLevel.DAMAGED
        return TrustLevel.UNSCORED_CLEAN if self.groq_unscored else TrustLevel.SCORED

    @property
    def may_select_offer(self) -> bool:
        """A clean unscored Groq voice reply may pick from Raven's own pending offer (06.11.2026)."""
        return self.caller_allows and self.voice and self.trust is TrustLevel.UNSCORED_CLEAN

    @property
    def explicit_local_request(self) -> bool:
        """Document/memory requests: reliable text, scored or explicitly unscored Groq."""
        return self.caller_allows and self.reliable and (self.allow_actions or self.groq_unscored)

    @property
    def proposal_quality(self) -> bool:
        return self.allow_actions and self.reliable

    def outcome(self, *, stop_phrase: bool) -> PolicyOutcome:
        """Pre-routing gate; a stop phrase passes only with a known confidence."""
        return turn_permission(self.allow_actions, self.incomplete,
                               stop=stop_phrase and self.confidence_known)

    def groq_candidate(self, natural_actions_enabled: bool) -> bool:
        """Separate unscored-Groq proposal path; still needs policy and confirmation."""
        return self.may_select_offer and natural_actions_enabled
