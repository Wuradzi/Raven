"""Primary whole-utterance STT; legacy constrained Vosk capture is untouched."""
import logging
import math
import time
from functools import partial
from types import SimpleNamespace

from core.listen import VoskListener
from core.models import RecognitionResult
from core.voice_diagnostics import backend_failure_reason
from core.performance import TurnTiming
from services.audio.backends import VoskBackend, create_backend
from services.audio.profiles import resolve_profile
from services.audio.endpoint import QuietEndpoint
from services.audio.pcm_capture import capture_pcm, sparse_activity

logger = logging.getLogger(__name__)

# Groq failures that justify the lazy local reserve: transport or service trouble, never a bad key or empty audio.
CLOUD_OUTAGE_REASONS = frozenset({'timeout', 'network', 'rate_limit', 'server'})
FALLBACK_NOTICES = {
    'engaged': 'Хмарне розпізнавання недоступне, переходжу на локальне.',
    'local_unavailable': 'Хмарне розпізнавання недоступне, а локальний резерв не готовий.',
    'exhausted': 'Хмарне розпізнавання досі недоступне; локальний резерв вимкнено до його відновлення.',
}


class SpeechListener(VoskListener):
    def __init__(self, settings):
        super().__init__(settings)
        self.vosk_backend = VoskBackend(self.model_path)
        self.profile, self.capabilities = resolve_profile(settings)
        logger.info('STT profile=%s backend=%s model=%s device=%s compute=%s arch=%s platform=%s',
                    self.profile.name, self.profile.backend, self.profile.model, self.profile.device,
                    self.profile.compute_type, self.capabilities.architecture, self.capabilities.platform)
        self.primary = None
        self._cloud_outage_streak = 0
        self._fallback_notice = None
        if self.profile.backend != 'vosk':
            self.primary = create_backend(settings, self.profile)
            if self.profile.backend == 'faster-whisper':
                self.whisper = self.primary.recognizer  # Existing lifecycle/performance owner.
            else:
                self.whisper.enabled = False  # Constrained Vosk never uses refinement.
        else:
            self.whisper.enabled = False  # Explicit Vosk-only low-resource backend.
            self.vosk_backend = create_backend(settings, self.profile)

    def _get_model(self):
        return self.vosk_backend.get_model()

    def prepare(self):
        # Warm the existing constrained recognizer before the first approval.
        # Uses VoskBackend's load lock; never downloads or changes grammar.
        if self.primary is not None and self.profile.backend != 'groq':
            confirmation_ok, confirmation_detail = self.vosk_backend.prepare()
            logger.info('stt.confirmation_prepare ready=%s detail=%s', confirmation_ok, confirmation_detail)
        ok, detail = self.primary.prepare() if self.primary is not None else self.vosk_backend.prepare()
        if not ok:
            logger.warning('STT prepare profile=%s: %s', self.profile.name, detail)
        return ok, detail

    def status(self):
        if self.primary is None:
            return True, 'Vosk (explicit low-resource backend)'
        if self.profile.backend == 'groq':
            ok, detail = self.primary.prepare()  # Offline key presence only.
            return ok, f'groq/{self.primary.metadata.model}; {detail}'
        ok, detail = (self.whisper.status() if self.profile.backend == 'faster-whisper'
                      else (False, 'experimental ONNX candidate; prepare explicitly, confidence unvalidated'))
        return ok, f'{self.profile.name}/{self.primary.metadata.backend}/{self.primary.metadata.model}; {detail}'

    def _cancelled(self):
        return self._closed.is_set() or self._paused.is_set() or self._interrupt.is_set()

    def listen_once(self, timeout_seconds=8.0, grammar=None):
        if (grammar is not None and self.profile.backend != 'groq') or self.primary is None:
            return super().listen_once(timeout_seconds, grammar)
        import sounddevice as sd
        self._interrupt.clear()
        if self._cancelled():
            return RecognitionResult('', 0, 'interrupted')
        errors = []
        for device, rate in self._input_candidates(sd):
            try:
                with self.performance.span('stt.capture_pcm'):
                    capture = capture_pcm(sd, device, rate, timeout_seconds, self.settings,
                                          self._cancelled, self._capture_state,
                                          utterance_timeout=(self.settings.stt_utterance_max_seconds
                                                             if grammar is None else None))
            except sd.PortAudioError as exc:
                errors.append(type(exc).__name__)
                continue
            self._resolved_device, self._resolved_sample_rate = device, rate
            if capture.interrupted or self._cancelled():
                return RecognitionResult('', 0, 'interrupted')
            if not capture.pcm:
                return RecognitionResult('', 0, 'whisper')
            timing = TurnTiming(self.performance, endpoint_at=capture.endpoint_at,
                                speech_end_at=capture.speech_end_at) if self.performance.enabled else None
            return self._recognize_capture(capture, timing)
        raise RuntimeError('STT microphone unavailable: ' + ', '.join(errors))

    def _recognize_capture(self, capture, timing=None):
        if capture.interrupted or self._cancelled():
            return RecognitionResult('', 0, 'interrupted')
        if not capture.pcm:
            return RecognitionResult('', 0, 'whisper')
        if self.profile.backend == 'groq' and sparse_activity(capture):
            logger.info('stt.audio_guard decision=reject reason=sparse_activity '
                        'active_audio_ms=%.1f speech_span_ms=%.1f',
                        capture.active_audio_ms, capture.speech_span_ms)
            return RecognitionResult('', 0, 'groq', timing=timing, fragmented=True,
                                     recognition_unreliable=True, confidence_available=False,
                                     capture_truncated=capture.truncated)
        stt_started = time.perf_counter()
        recognizer = self.primary
        if self.profile.backend == 'groq':
            from services.audio.groq_backend import GroqBackend
            dialogue = self.recognition_policy.dialogue
            pending = dialogue.pending
            if pending is not None and isinstance(self.primary, GroqBackend):
                def name_reply_allowed(text):
                    return (dialogue.pending is pending and dialogue.scoped_reply(text)
                            and dialogue.pending is pending and time.monotonic() < pending['expires'])
                recognizer = SimpleNamespace(transcribe=partial(
                    self.primary.transcribe, name_reply_allowed=name_reply_allowed))
        # Existing single-job drain keeps timed-out inference isolated. No Vosk
        # transcript is needed to start the primary decoder or finish capture.
        with self.performance.span('stt.primary_transcribe'):
            result, outcome, elapsed = self._refinement_wait.run(recognizer, capture.pcm, capture.sample_rate,
                min(4000, self.settings.stt_primary_timeout_ms), self.settings.stt_primary_timeout_ms,
                self._cancelled)
        if self._cancelled() or outcome == 'cancelled':
            return RecognitionResult('', 0, 'interrupted')
        failed = (outcome != 'done' or result.recognition_unreliable or not result.text.strip() or not math.isfinite(result.confidence)
                  or (result.confidence_available and result.confidence < self.settings.stt_whisper_min_confidence))
        reason = backend_failure_reason(outcome, self.primary)
        self._note_primary_outcome(outcome, reason)  # Primary verdict only: a reserve result never resets the streak.
        if failed:
            logger.warning('stt.fallback reason=%s target=%s model=%s', reason,
                           self.settings.stt_fallback_backend, self.primary.metadata.model)
            result, failed = self._fallback_result(result, capture, reason)
        if self._cancelled():
            return RecognitionResult('', 0, 'interrupted')
        result.utterance_incomplete = QuietEndpoint.possible_fragment(result.text)
        result.capture_truncated = capture.truncated
        result.recognition_unreliable = failed
        result.incomplete = failed or capture.truncated or result.utterance_incomplete
        result.fragmented = result.incomplete or not result.confidence_available
        result.timing = timing
        if timing is not None:
            timing.mark('recognition_ready')
        ready = time.perf_counter()
        metadata = getattr(getattr(self.primary, 'recognizer', None), 'metadata', {})
        worker_timings = getattr(getattr(self.primary, 'recognizer', None), 'last_timings', {})
        if outcome != 'done':
            metadata, worker_timings = {}, {}  # Never attribute a previous job's metadata to this turn.
        if timing is not None:
            timing.values.update(stt_inference=worker_timings.get('whisper.inference'),
                                 model=metadata.get('model', self.primary.metadata.model),
                                 device=metadata.get('device', 'n/a'), escalated=metadata.get('escalated'))
        logger.info('stt.latency capture_total_ms=%s speech_end_to_endpoint_ms=%s '
                    'endpoint_to_stt_start_ms=%.1f stt_inference_ms=%s endpoint_to_transcript_ms=%.1f '
                    'speech_end_to_transcript_ms=%s model=%s backend=%s device=%s compute_type=%s '
                    'fallback=%s escalated=%s cold_or_warm=%s outcome=%s',
                    (capture.capture_returned_at - capture.capture_started_at) * 1000
                    if capture.capture_started_at is not None and capture.capture_returned_at is not None else None,
                    (capture.endpoint_at - capture.speech_end_at) * 1000 if capture.speech_end_at is not None else None,
                    (stt_started - capture.endpoint_at) * 1000,
                    worker_timings.get('whisper.inference') if outcome == 'done' else None,
                    (ready - capture.endpoint_at) * 1000,
                    (ready - capture.speech_end_at) * 1000 if capture.speech_end_at is not None else None,
                    metadata.get('model', self.primary.metadata.model), self.primary.metadata.backend,
                    metadata.get('device', self.profile.device), metadata.get('compute_type', self.profile.compute_type),
                    'model_fallback_or_failure' if failed else metadata.get('fallback', 'none'),
                    metadata.get('escalated', False), metadata.get('cold_or_warm', 'unknown'), outcome)
        logger.info('stt.backend=%s model=%s language=uk task=transcribe latency_ms=%.1f '
                    'stt_final_engine=%s recognition_unreliable=%s capture_truncated=%s rtf=%.3f',
                    self.primary.metadata.backend, self.primary.metadata.model, elapsed,
                    result.engine, failed, capture.truncated,
                    elapsed / 1000 / max(.001, len(capture.pcm) / 2 / capture.sample_rate))
        return result

    def take_notice(self):
        """One user-facing notice about the local reserve (or None); reading clears it."""
        key, self._fallback_notice = self._fallback_notice, None
        return FALLBACK_NOTICES.get(key)

    def _fallback_mode(self, reason):
        if self.profile.backend != 'groq':
            return 'legacy' if self.settings.stt_fallback_backend == 'vosk' else None
        if not self.settings.stt_local_fallback_enabled or reason not in CLOUD_OUTAGE_REASONS:
            return None
        if self._cloud_outage_streak >= self.settings.stt_local_fallback_max_consecutive:
            if self._cloud_outage_streak == self.settings.stt_local_fallback_max_consecutive:
                self._cloud_outage_streak += 1  # Say it once, then stay silent until the cloud answers.
                self._fallback_notice = 'exhausted'
            return None
        return 'cloud'

    def _fallback_result(self, result, capture, reason):
        """(result, failed): local Vosk reserve for the utterance the primary recognizer could not read.

        The reserve loads lazily on first use, only while the cloud recognizer fails (and only if
        stt.local_fallback.enabled); it never replaces Groq as the primary. The legacy non-cloud
        fallback keeps its old rule: the reserve result still counts as unreliable.
        """
        mode = self._fallback_mode(reason)
        if mode is None:
            return result, True
        try:
            vosk_result = self.vosk_backend.transcribe(capture.pcm, capture.sample_rate)
        except Exception as exc:
            logger.warning('stt.fallback failed error=%s', type(exc).__name__)
            vosk_result = RecognitionResult('', 0, 'vosk')
            if mode == 'cloud':
                self._fallback_notice = 'local_unavailable'
        if mode == 'legacy':
            return vosk_result, True
        self._cloud_outage_streak += 1
        if self._fallback_notice is None and self._cloud_outage_streak == 1:
            self._fallback_notice = 'engaged'
        failed = not vosk_result.text.strip() or not math.isfinite(vosk_result.confidence)
        logger.info('stt.local_fallback engaged reason=%s streak=%s usable=%s',
                    reason, self._cloud_outage_streak, not failed)
        return vosk_result, failed

    def _note_primary_outcome(self, outcome, reason):
        """The cloud answered (even with silence or an unreadable utterance): the outage is over."""
        answered = outcome == 'done' and reason not in CLOUD_OUTAGE_REASONS
        if self.profile.backend == 'groq' and answered and self._cloud_outage_streak:
            logger.info('stt.local_fallback cloud_recovered streak=%s', self._cloud_outage_streak)
            self._cloud_outage_streak = 0

    def close(self):
        super().close()
        if self.primary is not None and self.profile.backend != 'faster-whisper':
            self.primary.close()
        self.vosk_backend.close()
