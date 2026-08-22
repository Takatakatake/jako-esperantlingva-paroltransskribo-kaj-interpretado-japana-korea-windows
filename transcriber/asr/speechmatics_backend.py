"""Speechmatics realtime WebSocket backend."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import AsyncGenerator, Dict, Optional
from urllib.parse import urlparse, urlunparse

import aiohttp

import websockets
from websockets import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

from ..config import SpeechmaticsConfig
from .base import StreamingTranscriptionBackend, TranscriptionBackendError, TranscriptSegment


class SpeechmaticsRealtimeError(TranscriptionBackendError):
    """Raised when communication with Speechmatics fails."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


# Queue sentinel: the realtime session has finished and no further
# transcripts will arrive (EndOfTranscript received, or shutdown gave up).
_STREAM_ENDED = object()


class SpeechmaticsRealtimeBackend(StreamingTranscriptionBackend):
    """Manage realtime transcription sessions with Speechmatics."""

    _CLOSE_ERROR_HINTS = {
        4001: ("not_authorised", "API key or temporary key was not accepted.", False),
        4003: ("not_allowed", "The account is not allowed to use the requested realtime action.", False),
        4004: (
            "invalid_model",
            "The requested language/model/operating_point is not available to this account.",
            False,
        ),
        4005: (
            "quota_exceeded",
            "The maximum number of concurrent realtime connections has been reached.",
            True,
        ),
        4006: (
            "timelimit_exceeded",
            "The realtime usage time quota for the Speechmatics contract has been reached.",
            False,
        ),
        4013: ("job_error", "Speechmatics could not start the realtime job.", True),
    }

    # Each failed recovery cycle spends ~20s inside connect()'s own retries,
    # so 8 cycles tolerate roughly 2-3 minutes of network outage
    # (suspend/resume, router reboot) before giving up for good.
    _MAX_CONSECUTIVE_RECOVERIES = 8

    def __init__(self, config: SpeechmaticsConfig) -> None:
        self.config = config
        self._websocket: Optional[WebSocketClientProtocol] = None
        self._listen_task: Optional[asyncio.Task[None]] = None
        self._transcript_queue: "asyncio.Queue[object]" = asyncio.Queue()
        self._connected = asyncio.Event()
        self._recognition_started = asyncio.Event()
        self._listener_error: Optional[SpeechmaticsRealtimeError] = None
        self._end_of_transcript = asyncio.Event()
        self._reconnect_guard = asyncio.Lock()
        self._seq_no = 0
        self._consecutive_recoveries = 0

    async def __aenter__(self) -> "SpeechmaticsRealtimeBackend":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        await self.close()

    async def connect(self) -> None:
        """Establish the websocket connection and send the start message."""

        self._connected.clear()
        self._recognition_started.clear()
        self._end_of_transcript.clear()
        self._listener_error = None
        self._seq_no = 0
        self._reset_transcript_queue()
        max_attempts = max(0, self.config.max_reconnect_attempts)
        backoff = max(self.config.reconnect_backoff_seconds, 0.1)
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= max_attempts:
            if attempt > 0:
                delay = backoff * (2 ** (attempt - 1))
                logging.warning(
                    "Speechmatics connection retry %d/%d in %.1f seconds.",
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

            try:
                ws_url, headers = await self._build_connection_params()
                await self._open_connection(ws_url, headers)
                logging.info("Connected to Speechmatics realtime endpoint.")
                return
            except SpeechmaticsRealtimeError as exc:
                last_exc = exc
                logging.error("Speechmatics connection attempt failed: %s", exc)
                await self.close()
                if not exc.retryable:
                    raise
            attempt += 1

        raise SpeechmaticsRealtimeError(
            f"Failed to connect to Speechmatics after {max_attempts + 1} attempts."
        ) from last_exc

    async def _authorize_jwt(self) -> Optional[str]:
        """Exchange API key for a short-lived JWT via HTTPS.

        Builds the authorize URL from the configured WebSocket endpoint by
        switching scheme to https and appending the authorize path.
        """

        api_key = (self.config.api_key or "").strip()
        if not api_key:
            return None

        # Management Platform temporary token endpoint (per official SDK semantics)
        # POST https://mp.speechmatics.com/v1/api_keys?type=rt&sm-sdk=python-<ver>
        # Headers: Authorization: Bearer <API_KEY>
        # Body: {"ttl": <seconds>, "region": "eu"|"us"|"ca"|"ap"}

        parsed_ws = urlparse(self.config.connection_url)
        region = self._infer_region_from_host(parsed_ws.hostname)

        endpoint = "https://mp.speechmatics.com/v1/api_keys"
        params = {"type": "rt", "sm-sdk": "python-custom"}
        payload = {"ttl": int(self.config.jwt_ttl_seconds), "region": region}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, params=params, json=payload, headers=headers) as resp:
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        logging.error("JWT authorize failed (%s): %s", resp.status, text)
                        return None
                    data = await resp.json()
                    token = data.get("key_value") or data.get("token") or data.get("jwt")
                    if not token:
                        logging.error("JWT authorize response missing token: %s", data)
                    return token
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("JWT authorize error: %s", exc)
            return None

    @staticmethod
    def _augment_ws_url_with_language(base_url: str, language: str) -> str:
        parsed = urlparse(base_url)
        path = parsed.path or "/v2"
        lang = (language or "").strip()
        if not lang:
            return base_url
        if not path.endswith(f"/{lang}"):
            if path.endswith("/"):
                path += lang
            else:
                path += f"/{lang}"
        return urlunparse(parsed._replace(path=path))

    @staticmethod
    def _infer_region_from_host(hostname: Optional[str]) -> str:
        """Map Speechmatics host prefixes to JWT regions."""

        if not hostname:
            return "eu"
        prefix = hostname.split(".")[0].lower()
        if prefix.startswith("eu"):
            return "eu"
        if prefix.startswith("us"):
            return "us"
        if prefix.startswith("ca"):
            return "ca"
        if prefix.startswith("ap"):
            return "ap"
        return "eu"

    async def _build_connection_params(self) -> tuple[str, Dict[str, str]]:
        ws_url = self._augment_ws_url_with_language(self.config.connection_url, self.config.language)
        token = (self.config.jwt_token or "").strip()
        if token:
            return ws_url, {"Authorization": f"Bearer {token}"}

        api_key = (self.config.api_key or "").strip()
        if self.config.auth_mode == "api_key":
            if not api_key:
                raise SpeechmaticsRealtimeError("SPEECHMATICS_API_KEY is required for Speechmatics.")
            return ws_url, {"Authorization": f"Bearer {api_key}"}

        token = await self._authorize_jwt()
        if not token:
            raise SpeechmaticsRealtimeError("Failed to obtain temporary key for Speechmatics.")
        headers = {"Authorization": f"Bearer {token}"}
        return ws_url, headers

    async def _open_connection(self, ws_url: str, headers: Dict[str, str]) -> None:
        try:
            self._websocket = await websockets.connect(
                ws_url,
                additional_headers=headers,
                max_size=4 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
                max_queue=32,
            )
        except Exception as exc:  # pylint: disable=broad-except
            raise SpeechmaticsRealtimeError(f"Failed to connect to Speechmatics: {exc}") from exc

        try:
            await self._send_start_message()
        except ConnectionClosed as exc:
            raise self._connection_closed_error(
                exc,
                "Speechmatics closed the connection while starting recognition",
            ) from exc
        self._connected.set()
        self._listen_task = asyncio.create_task(self._listen_loop(), name="speechmatics-listener")

    async def close(self) -> None:
        """Close the websocket connection gracefully."""

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    # close() itself was cancelled (e.g. forced shutdown);
                    # do not swallow that and continue running.
                    raise
            except Exception as exc:  # noqa: BLE001
                logging.debug("Listener task ended with error during close: %s", exc)
            self._listen_task = None

        if self._websocket:
            try:
                await self._websocket.close()
            finally:
                self._websocket = None
        self._connected.clear()
        self._recognition_started.clear()

    async def send_audio_chunk(self, chunk: bytes) -> None:
        """Send raw PCM audio, transparently re-establishing a dropped session.

        Retryable failures (network blips, server-side closes marked retryable)
        trigger a reconnect instead of ending the whole pipeline; only
        non-retryable errors or repeated back-to-back failures propagate.
        """

        try:
            await self._send_chunk_once(chunk)
            self._consecutive_recoveries = 0
            return
        except SpeechmaticsRealtimeError as exc:
            if not exc.retryable:
                raise
            self._consecutive_recoveries += 1
            if self._consecutive_recoveries > self._MAX_CONSECUTIVE_RECOVERIES:
                raise SpeechmaticsRealtimeError(
                    "Giving up after "
                    f"{self._consecutive_recoveries} consecutive session recoveries: {exc}",
                    retryable=False,
                ) from exc
            try:
                await self._recover_connection(exc)
            except SpeechmaticsRealtimeError as recover_exc:
                if not recover_exc.retryable:
                    raise
                # The network may still be down (suspend/resume, router
                # reboot). Drop this chunk and keep the session alive; the
                # next chunk retries recovery, bounded by the counter above.
                logging.warning(
                    "Session recovery %d/%d failed (%s); will retry on the next chunk.",
                    self._consecutive_recoveries,
                    self._MAX_CONSECUTIVE_RECOVERIES,
                    recover_exc,
                )
                return

        try:
            await self._send_chunk_once(chunk)
            self._consecutive_recoveries = 0
        except SpeechmaticsRealtimeError as exc:
            if not exc.retryable:
                raise
            logging.warning("Audio chunk dropped right after reconnecting (%s).", exc)

    async def _send_chunk_once(self, chunk: bytes) -> None:
        if self._listener_error is not None:
            raise self._listener_error
        if self._websocket is None or not self._connected.is_set():
            raise SpeechmaticsRealtimeError("Connection is not established.")
        if not self._recognition_started.is_set():
            try:
                await asyncio.wait_for(self._recognition_started.wait(), timeout=5.0)
            except asyncio.TimeoutError as exc:
                raise SpeechmaticsRealtimeError("Recognition did not start in time.") from exc
        try:
            await self._websocket.send(chunk)
        except ConnectionClosed as exc:
            raise self._connection_closed_error(
                exc,
                "Speechmatics closed the connection while streaming audio",
            ) from exc
        except Exception as exc:  # pylint: disable=broad-except
            raise SpeechmaticsRealtimeError(f"Failed to stream audio: {exc}") from exc
        self._seq_no += 1

    async def _recover_connection(self, reason: Exception) -> None:
        """Re-establish the realtime session after a retryable mid-stream failure.

        Always tears the session down and reconnects: several retryable
        failures (e.g. recognition never starting) leave the connection state
        looking healthy, so state checks cannot decide whether to skip.
        """

        async with self._reconnect_guard:
            logging.warning("Speechmatics session lost (%s); reconnecting...", reason)
            await self.close()
            await self.connect()
            logging.info("Speechmatics session re-established.")

    async def finish_stream(self, timeout: float = 8.0) -> None:
        """Ask Speechmatics to finalize trailing audio, then end the result stream.

        Sends EndOfStream with the number of audio chunks delivered and waits
        for EndOfTranscript so the last utterance still becomes a final.
        """

        websocket = self._websocket
        if websocket is None or not self._connected.is_set():
            await self._signal_stream_end()
            return
        try:
            await websocket.send(
                json.dumps({"message": "EndOfStream", "last_seq_no": self._seq_no})
            )
        except Exception as exc:  # noqa: BLE001
            logging.debug("Failed to send EndOfStream: %s", exc)
            await self._signal_stream_end()
            return
        try:
            await asyncio.wait_for(self._end_of_transcript.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logging.warning("Timed out waiting for Speechmatics EndOfTranscript; closing anyway.")
            await self._signal_stream_end()

    async def _signal_stream_end(self) -> None:
        await self._transcript_queue.put(_STREAM_ENDED)

    async def transcript_results(self) -> AsyncGenerator[TranscriptSegment, None]:
        """Yield transcript results as they arrive."""

        while True:
            result = await self._transcript_queue.get()
            if result is _STREAM_ENDED:
                return
            if result is None:
                error = self._listener_error
                if error is not None and not error.retryable:
                    raise error
                # Retryable failures are recovered by the audio sender; keep
                # consuming so transcripts resume after the reconnect.
                continue
            yield result

    async def _send_start_message(self) -> None:
        if self._websocket is None:
            raise SpeechmaticsRealtimeError("Websocket is not connected.")

        diarization_mode = "speaker" if self.config.enable_diarization else None
        start_message = {
            "message": "StartRecognition",
            "transcription_config": {
                "language": self.config.language,
                "enable_partials": True,
            },
            "audio_format": {
                "type": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.config.sample_rate,
            },
        }
        if self.config.operating_point != "standard":
            start_message["transcription_config"]["operating_point"] = self.config.operating_point
        if diarization_mode:
            start_message["transcription_config"]["diarization"] = diarization_mode
        await self._websocket.send(json.dumps(start_message))
        logging.debug("Sent Speechmatics StartRecognition: %s", start_message)

    @classmethod
    def _format_close_message(
        cls,
        *,
        context: str,
        code: Optional[int],
        reason: Optional[str],
        operating_point: str,
    ) -> tuple[str, bool]:
        label, hint, retryable = cls._CLOSE_ERROR_HINTS.get(
            code or 0,
            ("unknown_close", "No specific Speechmatics close-code hint is available.", True),
        )
        parts = [f"{context}: {label}"]
        if code is not None:
            parts.append(f"close_code={code}")
        if reason:
            parts.append(f"reason={reason}")
        parts.append(hint)
        if code == 4006:
            parts.append(
                "Speechmatics rejected StartRecognition before audio was processed; "
                "this is not caused by silent input or Linux loopback routing. "
                "Verify the API key, workspace, realtime entitlement, and contract usage "
                "in the Speechmatics portal. If the portal shows available realtime quota, "
                "contact Speechmatics support with close_code=4006 and reason=timelimit_exceeded."
            )
        elif code in {4003, 4004} and operating_point == "enhanced":
            parts.append(
                "This may mean enhanced is not enabled for the current API key; "
                "set SPEECHMATICS_OPERATING_POINT=standard to restore the standard model."
            )
        return " | ".join(parts), retryable

    def _connection_closed_error(self, exc: ConnectionClosed, context: str) -> SpeechmaticsRealtimeError:
        received = getattr(exc, "rcvd", None)
        code = getattr(received, "code", None)
        reason = getattr(received, "reason", None)
        message, retryable = self._format_close_message(
            context=context,
            code=code,
            reason=reason,
            operating_point=self.config.operating_point,
        )
        return SpeechmaticsRealtimeError(message, retryable=retryable)

    def _server_error_message(self, payload: Dict) -> SpeechmaticsRealtimeError:
        error_type = str(payload.get("type") or "unknown_error")
        reason = str(payload.get("reason") or "").strip()
        close_code = None
        for code, (label, _hint, _retryable) in self._CLOSE_ERROR_HINTS.items():
            if label == error_type:
                close_code = code
                break
        message, retryable = self._format_close_message(
            context="Speechmatics returned an error",
            code=close_code,
            reason=reason or error_type,
            operating_point=self.config.operating_point,
        )
        return SpeechmaticsRealtimeError(message, retryable=retryable)

    async def _listen_loop(self) -> None:
        """Receive transcript messages and push them into the queue."""

        assert self._websocket is not None  # nosec B101
        try:
            async for message in self._websocket:
                if isinstance(message, bytes):
                    logging.debug("Received binary %d bytes (ignored)", len(message))
                    continue
                payload = json.loads(message)
                msg_type = payload.get("message") or payload.get("type")
                if msg_type == "RecognitionStarted":
                    self._recognition_started.set()
                    logging.info("Recognition started.")
                elif msg_type in ("AddPartialTranscript", "AddTranscript"):
                    transcript = self._parse_transcript(payload)
                    if transcript:
                        await self._transcript_queue.put(transcript)
                elif msg_type == "EndOfTranscript":
                    logging.info("Speechmatics confirmed end of transcript.")
                    self._end_of_transcript.set()
                    await self._transcript_queue.put(_STREAM_ENDED)
                    break
                elif msg_type in ("Warning",):
                    logging.warning("Speechmatics warning: %s", payload)
                elif msg_type in ("Error", "error"):
                    error = self._server_error_message(payload)
                    logging.error("Speechmatics error: %s", error)
                    await self._handle_listener_failure(error)
                    break
                else:
                    logging.debug("Speechmatics message ignored: %s", payload)
        except asyncio.CancelledError:
            logging.info("Speechmatics listener cancelled.")
            raise
        except ConnectionClosed as exc:
            error = self._connection_closed_error(exc, "Speechmatics connection closed")
            logging.warning("%s", error)
            await self._handle_listener_failure(error)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("Error while listening to Speechmatics stream: %s", exc)
            await self._handle_listener_failure(
                SpeechmaticsRealtimeError("Speechmatics listener stopped unexpectedly.")
            )
        finally:
            self._connected.clear()
            self._recognition_started.clear()

    def _parse_transcript(self, payload: Dict) -> Optional[TranscriptSegment]:
        md = payload.get("metadata") or {}
        text = (md.get("transcript") or "").strip()
        if not text:
            return None
        is_final = (payload.get("message") == "AddTranscript")
        words = md.get("words") or []
        start_time = words[0].get("start_time") if words else None
        end_time = words[-1].get("end_time") if words else None

        transcript = TranscriptSegment(
            text=text,
            is_final=is_final,
            speaker=md.get("speaker"),
            start_time=start_time,
            end_time=end_time,
            raw=payload,
        )
        logging.debug(
            "Speechmatics transcript: %s (final=%s, speaker=%s)",
            transcript.text,
            transcript.is_final,
            transcript.speaker,
        )
        return transcript

    async def _handle_listener_failure(self, error: SpeechmaticsRealtimeError) -> None:
        if self._listener_error is None:
            self._listener_error = error
            if self._websocket and getattr(self._websocket, "close_code", None) is None:
                with contextlib.suppress(Exception):
                    await self._websocket.close(
                        code=1011,
                        reason="transcription_backend_error",
                    )
            await self._transcript_queue.put(None)

    def _reset_transcript_queue(self) -> None:
        old_queue = getattr(self, "_transcript_queue", None)
        new_queue: "asyncio.Queue[object]" = asyncio.Queue()
        if old_queue is not None:
            # Preserve transcripts already received but not yet consumed, so a
            # mid-session reconnect does not silently drop delivered finals.
            while True:
                try:
                    item = old_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, TranscriptSegment):
                    new_queue.put_nowait(item)
            with contextlib.suppress(asyncio.QueueFull):
                old_queue.put_nowait(None)
        self._transcript_queue = new_queue
