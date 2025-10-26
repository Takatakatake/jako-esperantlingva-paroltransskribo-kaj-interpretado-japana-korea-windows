"""Whisper (faster-whisper) transcription backend."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator, Optional

import numpy as np
from faster_whisper import WhisperModel  # type: ignore

from ..config import WhisperConfig
from .base import StreamingTranscriptionBackend, TranscriptSegment


class WhisperBackendError(Exception):
    """Raised when the Whisper backend fails."""


class WhisperStreamingBackend(StreamingTranscriptionBackend):
    """Chunked transcription using faster-whisper."""

    def __init__(self, config: WhisperConfig, sample_rate: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        try:
            self._model = WhisperModel(
                model_size_or_path=config.model_size,
                device=config.device,
                compute_type=config.compute_type,
            )
        except Exception as exc:  # pylint: disable=broad-except
            raise WhisperBackendError(f"Failed to load Whisper model: {exc}") from exc

        self._segment_samples = int(self.sample_rate * config.segment_duration)
        self._buffer = bytearray()
        self._queue: "asyncio.Queue[TranscriptSegment]" = asyncio.Queue()
        self._processed_samples = 0
        self._closed = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "WhisperStreamingBackend":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self._flush_buffer()
        self._closed = True

    async def send_audio_chunk(self, chunk: bytes) -> None:
        if self._closed:
            raise WhisperBackendError("Backend already closed.")
        async with self._lock:
            self._buffer.extend(chunk)
            await self._process_ready_segments()

    async def transcript_results(self) -> AsyncGenerator[TranscriptSegment, None]:
        while True:
            result = await self._queue.get()
            yield result

    async def _process_ready_segments(self) -> None:
        bytes_per_segment = self._segment_samples * 2  # int16
        while len(self._buffer) >= bytes_per_segment:
            segment_bytes = self._buffer[:bytes_per_segment]
            del self._buffer[:bytes_per_segment]
            await self._transcribe_segment(segment_bytes)

    async def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        # Flush remaining audio as a final short segment.
        await self._transcribe_segment(bytes(self._buffer), final_flush=True)
        self._buffer.clear()

    async def _transcribe_segment(self, segment_bytes: bytes, final_flush: bool = False) -> None:
        if not segment_bytes:
            return

        loop = asyncio.get_running_loop()
        audio_int16 = np.frombuffer(segment_bytes, dtype=np.int16)
        if audio_int16.size == 0:
            return

        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        try:
            segments_text = await loop.run_in_executor(
                None,
                self._run_transcription,
                audio_float32,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("Whisper transcription failed: %s", exc)
            raise WhisperBackendError("Whisper transcription failed.") from exc

        start_time = self._processed_samples / self.sample_rate
        end_time = (self._processed_samples + audio_int16.size) / self.sample_rate
        self._processed_samples += audio_int16.size

        if segments_text:
            await self._queue.put(
                TranscriptSegment(
                    text=segments_text,
                    is_final=True,
                    speaker=None,
                    start_time=start_time,
                    end_time=end_time,
                )
            )
        elif final_flush:
            # Emit silence marker to signal end.
            await self._queue.put(
                TranscriptSegment(
                    text="",
                    is_final=True,
                    speaker=None,
                    start_time=start_time,
                    end_time=end_time,
                )
            )

    def _run_transcription(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio=audio,
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            condition_on_previous_text=False,
        )

        texts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                texts.append(text)
        return " ".join(texts).strip()
