"""Abstractions shared by realtime transcription backends."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import AsyncGenerator, Optional


@dataclass
class TranscriptSegment:
    """Represents one transcription update."""

    text: str
    is_final: bool
    speaker: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    raw: Optional[dict] = None


class TranscriptionBackendError(Exception):
    """Base class for backend-specific transcription failures."""


class StreamingTranscriptionBackend(abc.ABC):
    """Interface for realtime speech-to-text streaming backends."""

    @abc.abstractmethod
    async def __aenter__(self) -> "StreamingTranscriptionBackend":  # pragma: no cover - protocol
        ...

    @abc.abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - protocol
        ...

    @abc.abstractmethod
    async def send_audio_chunk(self, chunk: bytes) -> None:
        """Stream raw PCM audio to the backend."""

    async def finish_stream(self, timeout: float = 8.0) -> None:
        """Flush trailing results before close; backends without a
        finalization handshake need no work here."""

        return None

    @abc.abstractmethod
    async def transcript_results(self) -> AsyncGenerator[TranscriptSegment, None]:
        """Yield successive transcript segments."""
