"""Streaming ASR backends for the Esperanto transcription toolkit."""

from .base import StreamingTranscriptionBackend, TranscriptSegment
from .speechmatics_backend import SpeechmaticsRealtimeBackend, SpeechmaticsRealtimeError
from .vosk_backend import VoskStreamingBackend, VoskBackendError
from .whisper_backend import WhisperBackendError, WhisperStreamingBackend

__all__ = [
    "StreamingTranscriptionBackend",
    "TranscriptSegment",
    "SpeechmaticsRealtimeBackend",
    "SpeechmaticsRealtimeError",
    "VoskStreamingBackend",
    "VoskBackendError",
    "WhisperStreamingBackend",
    "WhisperBackendError",
]
