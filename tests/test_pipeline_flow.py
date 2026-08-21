"""Tests for graceful shutdown drain and non-blocking caption emission."""

from __future__ import annotations

import asyncio
import unittest
from typing import List, Optional

from transcriber.asr.base import StreamingTranscriptionBackend, TranscriptSegment
from transcriber.config import Settings
from transcriber.pipeline import TranscriptionPipeline
from transcriber.translate.service import TranslationResult


class _RecordingWebUI:
    def __init__(self) -> None:
        self.messages: List[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.messages.append(payload)


class _FakeAudioStream:
    """Blocks until stop() is called, then ends iteration like the real stream."""

    def __init__(self) -> None:
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    def __aiter__(self) -> "_FakeAudioStream":
        return self

    async def __anext__(self) -> bytes:
        await self._stopped.wait()
        raise StopAsyncIteration


class _FakeBackend(StreamingTranscriptionBackend):
    """Delivers a trailing final only when finish_stream() is called."""

    def __init__(self, tail_text: str = "Lasta frazo.") -> None:
        self._queue: "asyncio.Queue[Optional[TranscriptSegment]]" = asyncio.Queue()
        self._tail_text = tail_text
        self.finish_called = False

    async def __aenter__(self) -> "_FakeBackend":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def send_audio_chunk(self, chunk: bytes) -> None:
        return None

    async def finish_stream(self, timeout: float = 8.0) -> None:
        self.finish_called = True
        await self._queue.put(TranscriptSegment(text=self._tail_text, is_final=True))
        await self._queue.put(None)

    async def transcript_results(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


class _NoDrainBackend(StreamingTranscriptionBackend):
    """Mimics vosk/whisper: no finish_stream override, results never end."""

    def __init__(self) -> None:
        self._queue: "asyncio.Queue[TranscriptSegment]" = asyncio.Queue()

    async def __aenter__(self) -> "_NoDrainBackend":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    async def send_audio_chunk(self, chunk: bytes) -> None:
        return None

    async def transcript_results(self):
        while True:
            yield await self._queue.get()


class PipelineGracefulShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_stop_drains_tail_final(self) -> None:
        pipeline = TranscriptionPipeline(Settings())
        backend = _FakeBackend()
        stream = _FakeAudioStream()

        loop_task = asyncio.create_task(pipeline._main_loop(stream, backend))
        await asyncio.sleep(0.05)
        pipeline.request_stop()
        await asyncio.wait_for(loop_task, timeout=5.0)

        self.assertTrue(backend.finish_called)
        self.assertIn("Lasta frazo.", pipeline.state.final_transcripts)

    async def test_request_stop_without_drain_support_ends_promptly(self) -> None:
        """Backends without finish_stream must not stall the shutdown for 10s."""

        pipeline = TranscriptionPipeline(Settings())
        backend = _NoDrainBackend()
        stream = _FakeAudioStream()

        loop_task = asyncio.create_task(pipeline._main_loop(stream, backend))
        await asyncio.sleep(0.05)
        start = asyncio.get_running_loop().time()
        pipeline.request_stop()
        await asyncio.wait_for(loop_task, timeout=5.0)

        self.assertLess(asyncio.get_running_loop().time() - start, 4.0)


class PipelineEmitOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_is_published_before_translation_completes(self) -> None:
        pipeline = TranscriptionPipeline(Settings())
        web = _RecordingWebUI()
        pipeline._web_ui = web  # type: ignore[assignment]
        pipeline._translation_targets = ["ja"]
        pipeline._translation_queue = asyncio.Queue(maxsize=4)
        release = asyncio.Event()

        async def slow_translate(sentence: str) -> TranslationResult:
            await release.wait()
            return TranslationResult(text=sentence, translations={"ja": "こんにちは。"})

        pipeline._translation_service.translate = slow_translate  # type: ignore[method-assign]
        pipeline._translation_worker = asyncio.create_task(
            pipeline._translation_worker_loop()
        )

        await asyncio.wait_for(pipeline._emit_sentence("Saluton.", None), timeout=2.0)

        self.assertEqual(web.messages[0]["type"], "final")
        self.assertEqual(web.messages[0]["text"], "Saluton.")
        self.assertEqual(web.messages[0]["translations"], {})
        self.assertTrue(web.messages[0]["translationsPending"])

        release.set()
        await asyncio.sleep(0.1)

        translation_messages = [m for m in web.messages if m["type"] == "translation"]
        self.assertEqual(len(translation_messages), 1)
        self.assertEqual(translation_messages[0]["id"], web.messages[0]["id"])
        self.assertEqual(translation_messages[0]["translations"], {"ja": "こんにちは。"})

        await pipeline._stop_translation_worker()

    async def test_translation_queue_overflow_still_delivers_caption_everywhere(self) -> None:
        pipeline = TranscriptionPipeline(Settings())
        web = _RecordingWebUI()
        pipeline._web_ui = web  # type: ignore[assignment]
        pipeline._translation_targets = ["ja"]
        pipeline._translation_queue = asyncio.Queue(maxsize=1)
        pipeline._translation_queue.put_nowait((0, "占有中"))

        await pipeline._emit_sentence("Plena vico.", None)

        self.assertEqual(web.messages[0]["type"], "final")
        translation_messages = [m for m in web.messages if m["type"] == "translation"]
        self.assertEqual(len(translation_messages), 1)
        self.assertEqual(translation_messages[0]["translations"], {})
        self.assertIn("Plena vico.", pipeline.state.final_transcripts)


if __name__ == "__main__":
    unittest.main()
