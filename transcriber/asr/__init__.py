"""Streaming ASR backends for the Esperanto transcription toolkit."""

from .base import StreamingTranscriptionBackend, TranscriptionBackendError, TranscriptSegment

_LAZY_EXPORTS = {
    "SpeechmaticsRealtimeBackend": ("speechmatics_backend", "SpeechmaticsRealtimeBackend"),
    "SpeechmaticsRealtimeError": ("speechmatics_backend", "SpeechmaticsRealtimeError"),
    "VoskStreamingBackend": ("vosk_backend", "VoskStreamingBackend"),
    "VoskBackendError": ("vosk_backend", "VoskBackendError"),
    "WhisperStreamingBackend": ("whisper_backend", "WhisperStreamingBackend"),
    "WhisperBackendError": ("whisper_backend", "WhisperBackendError"),
}


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(f"{__name__}.{module_name}", fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

__all__ = [
    "StreamingTranscriptionBackend",
    "TranscriptionBackendError",
    "TranscriptSegment",
    "SpeechmaticsRealtimeBackend",
    "SpeechmaticsRealtimeError",
    "VoskStreamingBackend",
    "VoskBackendError",
    "WhisperStreamingBackend",
    "WhisperBackendError",
]
