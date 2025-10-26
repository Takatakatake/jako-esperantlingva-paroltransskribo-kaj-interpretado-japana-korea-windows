"""Vosk offline transcription backend."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from vosk import KaldiRecognizer, Model  # type: ignore

from ..config import VoskConfig
from .base import StreamingTranscriptionBackend, TranscriptSegment


class VoskBackendError(Exception):
    """Raised when Vosk streaming fails."""


class VoskStreamingBackend(StreamingTranscriptionBackend):
    """Lightweight offline transcription using Vosk."""

    def __init__(self, config: VoskConfig) -> None:
        self.config = config
        try:
            self._model = Model(model_path=config.model_path)
        except Exception as exc:  # pylint: disable=broad-except
            raise VoskBackendError(f"Failed to load Vosk model: {exc}") from exc

        self._recognizer = KaldiRecognizer(self._model, config.sample_rate)
        self._recognizer.SetWords(True)
        self._queue: "asyncio.Queue[TranscriptSegment]" = asyncio.Queue()
        self._last_partial: Optional[str] = None
        self._closed = False

    async def __aenter__(self) -> "VoskStreamingBackend":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self._closed = True

    async def send_audio_chunk(self, chunk: bytes) -> None:
        if self._closed:
            raise VoskBackendError("Backend already closed.")

        accepted = self._recognizer.AcceptWaveform(chunk)
        if accepted:
            await self._emit_result(self._recognizer.Result(), is_final=True)
        elif self.config.enable_partials:
            await self._emit_result(self._recognizer.PartialResult(), is_final=False)

    async def transcript_results(self) -> AsyncGenerator[TranscriptSegment, None]:
        while True:
            result = await self._queue.get()
            yield result

    async def _emit_result(self, result_text: str, is_final: bool) -> None:
        if not result_text:
            return
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError as exc:
            logging.debug("Invalid Vosk JSON payload %s: %s", result_text, exc)
            return

        text = payload.get("text", "").strip()
        if not text:
            if not is_final and self._last_partial:
                # Reset partial when Vosk clears interim hypothesis.
                await self._queue.put(
                    TranscriptSegment(text="", is_final=False, raw=payload)
                )
                self._last_partial = None
            return

        if not is_final:
            if text == self._last_partial:
                return
            self._last_partial = text

        words = payload.get("result") or []
        start_time = words[0].get("start") if words else None
        end_time = words[-1].get("end") if words else None

        segment = TranscriptSegment(
            text=text,
            is_final=is_final,
            speaker=None,
            start_time=start_time,
            end_time=end_time,
            raw=payload,
        )
        await self._queue.put(segment)

        if is_final:
            self._last_partial = None
