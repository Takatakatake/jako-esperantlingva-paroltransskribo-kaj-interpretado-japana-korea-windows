"""High-level orchestration of realtime transcription pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .asr.base import StreamingTranscriptionBackend, TranscriptionBackendError
from .audio import AudioCaptureError, AudioChunkStream
from .audio_setup import AudioEnvironmentError, AudioEnvironmentManager
from .config import BackendChoice, Settings, load_settings
from .zoom_caption import ZoomCaptionPublisher
from .display.webui import CaptionWebUI
from .discord import DiscordBatcher, DiscordNotifier
from .translate import TranslationService


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    normalized = re.sub(r"\s+", " ", stripped)
    normalized = re.sub(r"\s+([,.;:?!])", r"\1", normalized)
    normalized = re.sub(r"([\(\[\{])\s+", r"\1", normalized)
    normalized = re.sub(r"\s+([\)\]\}])", r"\1", normalized)
    return normalized


@dataclass
class PipelineState:
    """Tracks transcription state for downstream consumers."""

    final_transcripts: List[str] = field(default_factory=list)
    latest_partial: Optional[str] = None

    def add_result(self, text: str, is_final: bool) -> Optional[str]:
        if is_final:
            if text:
                self.final_transcripts.append(text)
                self.latest_partial = None
                return text
            return None

        self.latest_partial = text
        return None


class SentenceAssembler:
    """Accumulate short fragments into sentence-sized chunks."""

    def __init__(self, max_length: int = 120) -> None:
        self._buffer: str = ""
        self._max_length = max_length

    def feed(self, fragment: str) -> List[str]:
        fragment = fragment.strip()
        if not fragment:
            return []

        if self._buffer:
            self._buffer = f"{self._buffer} {fragment}".strip()
        else:
            self._buffer = fragment

        sentences: List[str] = []
        if self._buffer and (self._buffer[-1] in ".?!" or len(self._buffer) >= self._max_length):
            sentences.append(self._buffer)
            self._buffer = ""
        return sentences

    @property
    def pending(self) -> str:
        return self._buffer

    def flush(self) -> List[str]:
        if not self._buffer:
            return []
        pending = self._buffer
        self._buffer = ""
        return [pending]


class TranscriptFileLogger:
    """Handles optional transcript persistence."""

    def __init__(self, settings, override_path: Optional[str] = None) -> None:
        self._settings = settings
        self._override_path = override_path
        self._file = None

    @property
    def _resolved_path(self) -> Optional[Path]:
        if self._override_path:
            return Path(self._override_path).expanduser()
        if self._settings.file_path:
            return Path(self._settings.file_path).expanduser()
        return None

    def __enter__(self) -> "TranscriptFileLogger":
        path = self._resolved_path
        # An explicit --log-file override always logs; otherwise honor the
        # configured enabled flag (TRANSCRIPT_LOG_ENABLED=false wins even
        # when TRANSCRIPT_LOG_PATH is set).
        should_enable = bool(self._override_path) or self._settings.enabled
        if not should_enable or path is None:
            return self

        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if self._settings.overwrite else "a"
        self._file = path.open(mode=mode, encoding="utf-8")
        logging.info("Transcript logging to %s (mode=%s)", path, mode)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._file:
            self._file.close()
            self._file = None

    def log_final(self, text: str) -> None:
        if not self._file or not text:
            return

        line = text
        if self._settings.include_timestamps:
            timestamp = datetime.now().isoformat(timespec="seconds")
            line = f"[{timestamp}] {text}"
        self._file.write(line + "\n")
        self._file.flush()


class TranscriptionPipeline:
    """Coordinate audio capture, streaming transcription, and Zoom publishing."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        backend_override: Optional[str] = None,
        transcript_log_override: Optional[str] = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.backend_choice = (
            BackendChoice(backend_override.lower())
            if backend_override
            else self.settings.backend
        )
        self._audio_env = AudioEnvironmentManager(self.settings.audio)
        self._audio_stream = AudioChunkStream(
            self.settings.audio,
            check_interval=self.settings.audio.device_check_interval,
        )
        self._zoom_publisher = ZoomCaptionPublisher(self.settings.zoom)
        self._transcript_logger = TranscriptFileLogger(
            self.settings.logging, override_path=transcript_log_override
        )
        self._web_ui: Optional[CaptionWebUI] = None
        self.state = PipelineState()
        self._running = False
        self._stop_requested = asyncio.Event()
        self._caption_seq = 0
        self._translation_queue: Optional[asyncio.Queue] = None
        self._translation_worker: Optional[asyncio.Task] = None
        self._routing_watchdog: Optional[asyncio.Task] = None
        self._sentence_assembler = SentenceAssembler()
        self._discord_notifier = DiscordNotifier(
            webhook_url=self.settings.discord.webhook_url,
            username=self.settings.discord.username,
            enabled=self.settings.discord.enabled,
        )
        self._discord_batcher = DiscordBatcher(
            notifier=self._discord_notifier,
            flush_interval=self.settings.discord.batch_flush_interval,
            max_chars=self.settings.discord.batch_max_chars,
        )
        translation_cfg = self.settings.translation
        self._translation_targets = list(translation_cfg.targets)
        self._translation_defaults = {
            lang: translation_cfg.default_visibility.get(lang, True)
            for lang in self._translation_targets
        }
        self._translation_service = TranslationService(
            enabled=translation_cfg.enabled,
            source_language=translation_cfg.source_language,
            targets=translation_cfg.targets,
            provider=translation_cfg.provider,
            libre_url=translation_cfg.libre_url,
            libre_api_key=translation_cfg.libre_api_key,
            timeout=translation_cfg.timeout_seconds,
            google_api_key=translation_cfg.google_api_key,
            google_model=translation_cfg.google_model,
            google_credentials_path=translation_cfg.google_credentials_path,
            cache_ttl_seconds=translation_cfg.timeout_seconds * 4,
        )

    def request_stop(self) -> None:
        """Ask the running pipeline to shut down gracefully, flushing the tail."""

        self._stop_requested.set()

    async def run(self) -> None:
        """Run the pipeline until cancelled."""

        if self._running:
            raise RuntimeError("Pipeline already running.")
        self._running = True
        logging.info("Starting transcription pipeline with backend=%s.", self.backend_choice.value)

        try:
            self._audio_env.prepare()
        except AudioEnvironmentError as exc:
            logging.error("Audio environment preparation failed: %s", exc)
            self._running = False
            raise

        backend = self._create_backend()
        if self._translation_service.enabled:
            self._translation_queue = asyncio.Queue(maxsize=32)
            self._translation_worker = asyncio.create_task(
                self._translation_worker_loop(), name="translation-worker"
            )
        self._routing_watchdog = asyncio.create_task(
            self._routing_watchdog_loop(), name="routing-watchdog"
        )
        try:
            with self._transcript_logger:
                async with self._zoom_publisher:
                    if self.settings.web.enabled:
                        self._web_ui = CaptionWebUI(
                            host=self.settings.web.host,
                            port=self.settings.web.port,
                            translation_targets=self._translation_targets,
                            translation_default_visibility=self._translation_defaults,
                        )
                        try:
                            await self._web_ui.start()
                        except OSError as exc:
                            logging.error(
                                "Caption Web UI failed to start (%s). Port %s:%s in use?",
                                exc,
                                self.settings.web.host,
                                self.settings.web.port,
                            )
                            self._web_ui = None
                        else:
                            if self.settings.web.open_browser:
                                url = f"http://{self.settings.web.host}:{self._web_ui.port}"
                                logging.info("Opening caption board in default browser: %s", url)
                                try:
                                    if sys.platform.startswith("win"):
                                        os.startfile(url)  # type: ignore[attr-defined]
                                    else:
                                        loop = asyncio.get_running_loop()
                                        await loop.run_in_executor(
                                            None, functools.partial(webbrowser.open, url, new=1)
                                        )
                                except Exception as exc:  # noqa: BLE001
                                    logging.warning("Failed to open browser automatically: %s", exc)
                    async with self._audio_stream.connect() as audio_stream:
                        async with backend:
                            await self._main_loop(audio_stream, backend)
        except AudioCaptureError as exc:
            logging.error("Pipeline stopped due to error: %s", exc)
            raise
        except TranscriptionBackendError as exc:
            logging.error("Pipeline stopped due to error: %s", exc)
            raise
        finally:
            # A late cancellation (second Ctrl+C, shutdown timeout) must not
            # abort the remaining steps: audio routing restoration in
            # _audio_env.cleanup() has to run no matter what.
            cancelled_during_cleanup = False

            async def _cleanup_step(factory, label: str) -> None:
                nonlocal cancelled_during_cleanup
                try:
                    await factory()
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
                    logging.debug("Cleanup step %s interrupted by cancellation; continuing.", label)
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Cleanup step %s failed: %s", label, exc)

            await _cleanup_step(self._stop_routing_watchdog, "routing-watchdog")
            await _cleanup_step(self._flush_pending_sentences, "flush-pending-sentences")
            await _cleanup_step(self._stop_translation_worker, "translation-worker")
            if self._web_ui:
                await _cleanup_step(self._web_ui.stop, "web-ui")
                self._web_ui = None
            await _cleanup_step(self._discord_batcher.close, "discord-batcher")
            await _cleanup_step(self._discord_notifier.close, "discord-notifier")
            await _cleanup_step(self._translation_service.close, "translation-service")
            self._audio_env.cleanup()
            self._running = False
            logging.info("Transcription pipeline stopped.")
            if cancelled_during_cleanup and sys.exc_info()[0] is None:
                raise asyncio.CancelledError()

    async def _main_loop(
        self, audio_stream: AudioChunkStream, backend: StreamingTranscriptionBackend
    ) -> None:
        audio_task = asyncio.create_task(
            self._pump_audio(audio_stream, backend), name="audio-producer"
        )
        transcript_task = asyncio.create_task(
            self._consume_transcripts(backend), name="transcript-consumer"
        )
        stop_task = asyncio.create_task(self._stop_requested.wait(), name="stop-waiter")
        tasks = [audio_task, transcript_task]
        first_error: Optional[BaseException] = None
        cancelled = False
        graceful = False

        try:
            done, _pending = await asyncio.wait(
                {audio_task, transcript_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            graceful = stop_task in done or (
                audio_task in done
                and not audio_task.cancelled()
                and audio_task.exception() is None
            )
            if graceful:
                await self._finalize_session(audio_stream, backend, audio_task, transcript_task)
            else:
                for task in (audio_task, transcript_task):
                    if task not in done:
                        continue
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as exc:  # noqa: BLE001
                        first_error = exc
                        break
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            stop_task.cancel()
            for task in tasks:
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(*tasks, stop_task, return_exceptions=True)
            if not cancelled and not graceful and first_error is None:
                for result in results[:2]:
                    if isinstance(result, Exception) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        first_error = result
                        break

        if first_error is not None:
            raise first_error

    async def _finalize_session(
        self,
        audio_stream: AudioChunkStream,
        backend: StreamingTranscriptionBackend,
        audio_task: asyncio.Task,
        transcript_task: asyncio.Task,
    ) -> None:
        """Stop capture, flush trailing audio server-side, and drain final transcripts."""

        logging.info("Shutting down: flushing trailing audio and waiting for final transcripts...")
        audio_stream.stop()
        done, _pending = await asyncio.wait({audio_task}, timeout=3.0)
        if audio_task not in done:
            audio_task.cancel()
        try:
            await backend.finish_stream()
        except Exception as exc:  # noqa: BLE001
            logging.debug("finish_stream failed: %s", exc)
        supports_drain = (
            type(backend).finish_stream is not StreamingTranscriptionBackend.finish_stream
        )
        if supports_drain:
            done, _pending = await asyncio.wait({transcript_task}, timeout=10.0)
            if transcript_task not in done:
                logging.warning("Timed out draining final transcripts; trailing text may be lost.")
                transcript_task.cancel()
        else:
            # Backends without a finalization handshake never end their result
            # stream; waiting would just stall the shutdown.
            transcript_task.cancel()
        for task in (audio_task, transcript_task):
            if task.done() and not task.cancelled() and task.exception() is not None:
                logging.debug(
                    "Task %s ended with error during shutdown: %s",
                    task.get_name(),
                    task.exception(),
                )

    def _create_backend(self) -> StreamingTranscriptionBackend:
        if self.backend_choice is BackendChoice.SPEECHMATICS:
            if not self.settings.speechmatics:
                raise RuntimeError("Speechmatics configuration missing.")
            from .asr.speechmatics_backend import SpeechmaticsRealtimeBackend

            return SpeechmaticsRealtimeBackend(self.settings.speechmatics)

        if self.backend_choice is BackendChoice.VOSK:
            if not self.settings.vosk:
                raise RuntimeError("Vosk configuration missing.")
            from .asr.vosk_backend import VoskStreamingBackend

            return VoskStreamingBackend(self.settings.vosk)

        if self.backend_choice is BackendChoice.WHISPER:
            if not self.settings.whisper:
                raise RuntimeError("Whisper configuration missing.")
            from .asr.whisper_backend import WhisperStreamingBackend

            return WhisperStreamingBackend(
                self.settings.whisper, self.settings.audio.sample_rate
            )

        raise RuntimeError(f"Unsupported backend: {self.backend_choice}")

    async def _emit_sentence(self, sentence: str, speaker: Optional[str]) -> None:
        """Publish a final sentence immediately; translations follow asynchronously.

        The caption, transcript log, and Zoom post must never wait on the
        translation provider — a slow or failing provider would otherwise
        stall every downstream consumer.
        """

        sentence = sentence.strip()
        if not sentence:
            return

        self._caption_seq += 1
        entry_id = self._caption_seq

        logging.info("Final: %s", sentence)
        self._transcript_logger.log_final(sentence)

        translations_pending = (
            self._translation_queue is not None and bool(self._translation_targets)
        )
        if self._web_ui:
            await self._web_ui.broadcast(
                {
                    "type": "final",
                    "id": entry_id,
                    "text": sentence,
                    "speaker": speaker,
                    "translations": {},
                    "translationsPending": translations_pending,
                }
            )

        if translations_pending:
            assert self._translation_queue is not None
            try:
                self._translation_queue.put_nowait((entry_id, sentence))
            except asyncio.QueueFull:
                logging.warning(
                    "Translation backlog full; caption delivered without translation: %.60s",
                    sentence,
                )
                if self._web_ui:
                    await self._web_ui.broadcast(
                        {
                            "type": "translation",
                            "id": entry_id,
                            "text": sentence,
                            "translations": {},
                        }
                    )
                await self._discord_batcher.add_entry(sentence, {})
        else:
            await self._discord_batcher.add_entry(sentence, {})

        zoom_payload = self.state.add_result(sentence, True)
        if zoom_payload:
            await self._zoom_publisher.post_caption(zoom_payload)

    async def _translation_worker_loop(self) -> None:
        """Translate finals in order without blocking the caption path."""

        assert self._translation_queue is not None
        while True:
            item = await self._translation_queue.get()
            if item is None:
                break
            entry_id, sentence = item
            translations = await self._translate_with_deadline(sentence)
            if self._web_ui:
                await self._web_ui.broadcast(
                    {
                        "type": "translation",
                        "id": entry_id,
                        "text": sentence,
                        "translations": translations,
                    }
                )
            await self._discord_batcher.add_entry(sentence, translations)

    async def _translate_with_deadline(self, sentence: str) -> Dict[str, str]:
        deadline = max(self.settings.translation.timeout_seconds * 2.0, 10.0)
        try:
            result = await asyncio.wait_for(
                self._translation_service.translate(sentence), timeout=deadline
            )
            return result.translations
        except asyncio.TimeoutError:
            logging.warning("Translation timed out after %.0fs: %.60s", deadline, sentence)
        except Exception as exc:  # noqa: BLE001
            logging.error("Translation failed: %s", exc)
        return {}

    async def _routing_watchdog_loop(self) -> None:
        """Periodically re-pin the default input if the OS moved it mid-call
        (Bluetooth headset connecting, sound-settings changes, WirePlumber)."""

        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(5.0)
            try:
                await loop.run_in_executor(None, self._audio_env.enforce_default_source)
            except Exception as exc:  # noqa: BLE001
                logging.debug("Routing watchdog check failed: %s", exc)

    async def _stop_routing_watchdog(self) -> None:
        if self._routing_watchdog is None:
            return
        self._routing_watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._routing_watchdog
        self._routing_watchdog = None

    async def _stop_translation_worker(self) -> None:
        if self._translation_worker is None:
            return
        if self._translation_queue is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self._translation_queue.put_nowait(None)
        done, _pending = await asyncio.wait({self._translation_worker}, timeout=6.0)
        if self._translation_worker not in done:
            logging.warning("Translation worker did not drain in time; cancelling.")
            self._translation_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._translation_worker
        elif self._translation_worker.exception() is not None:
            logging.error(
                "Translation worker ended with error: %s", self._translation_worker.exception()
            )
        self._translation_worker = None
        self._translation_queue = None

    async def _flush_pending_sentences(self) -> None:
        pending_sentences = self._sentence_assembler.flush()
        for sentence in pending_sentences:
            await self._emit_sentence(sentence, speaker=None)
        if self._web_ui:
            await self._web_ui.broadcast(
                {
                    "type": "partial",
                    "text": "",
                    "speaker": None,
                }
            )

    async def _pump_audio(
        self, audio_stream: AudioChunkStream, backend: StreamingTranscriptionBackend
    ) -> None:
        async for chunk in audio_stream:
            await backend.send_audio_chunk(chunk)

    async def _consume_transcripts(self, backend: StreamingTranscriptionBackend) -> None:
        async for result in backend.transcript_results():
            if result.is_final:
                clean_text = _normalize_text(result.text)
                sentences = self._sentence_assembler.feed(clean_text)
                if sentences:
                    for sentence in sentences:
                        await self._emit_sentence(sentence, result.speaker)
                pending = self._sentence_assembler.pending
                if self._web_ui:
                    await self._web_ui.broadcast(
                        {
                            "type": "partial",
                            "text": pending,
                            "speaker": result.speaker,
                        }
                    )
            else:
                clean_partial = _normalize_text(result.text)
                if clean_partial:
                    logging.debug("Partial: %s", clean_partial)
                    if self._web_ui:
                        await self._web_ui.broadcast(
                            {
                                "type": "partial",
                                "text": clean_partial,
                                "speaker": result.speaker,
                            }
                        )
                zoom_payload = self.state.add_result(clean_partial, False)
                if zoom_payload:
                    await self._zoom_publisher.post_caption(zoom_payload)

    async def shutdown(self) -> None:
        """Cancel any running tasks (best-effort)."""

        await self._zoom_publisher.close()
