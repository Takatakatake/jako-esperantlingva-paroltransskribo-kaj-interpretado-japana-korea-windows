"""Unit tests for the Speechmatics realtime backend."""

from __future__ import annotations

import json
import unittest

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from transcriber.asr.base import TranscriptSegment
from transcriber.asr.speechmatics_backend import (
    SpeechmaticsRealtimeBackend,
    SpeechmaticsRealtimeError,
    _STREAM_ENDED,
)
from transcriber.config import SpeechmaticsConfig


def _make_backend() -> SpeechmaticsRealtimeBackend:
    return SpeechmaticsRealtimeBackend(
        SpeechmaticsConfig(api_key="sk_test_1234567890", language="eo")
    )


class _FakeWebsocket:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.sent_messages: list[str] = []
        self.exc = exc
        self.close_calls: list[tuple[int, str]] = []
        self.close_code = None

    async def send(self, message: str) -> None:
        if self.exc is not None:
            raise self.exc
        self.sent_messages.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


class SpeechmaticsBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_params_use_api_key_when_requested(self) -> None:
        config = SpeechmaticsConfig(
            api_key="sk_test_1234567890",
            language="eo",
            auth_mode="api_key",
            connection_url="wss://eu2.rt.speechmatics.com/v2",
        )
        backend = SpeechmaticsRealtimeBackend(config)

        ws_url, headers = await backend._build_connection_params()

        self.assertEqual(ws_url, "wss://eu2.rt.speechmatics.com/v2/eo")
        self.assertEqual(headers["Authorization"], "Bearer sk_test_1234567890")

    async def test_connection_params_default_to_temporary_key_mode(self) -> None:
        config = SpeechmaticsConfig(
            api_key="sk_test_1234567890",
            language="eo",
            connection_url="wss://eu2.rt.speechmatics.com/v2",
        )
        backend = SpeechmaticsRealtimeBackend(config)

        async def fake_authorize() -> str:
            return "tmp_test_token"

        backend._authorize_jwt = fake_authorize  # type: ignore[method-assign]

        ws_url, headers = await backend._build_connection_params()

        self.assertEqual(ws_url, "wss://eu2.rt.speechmatics.com/v2/eo")
        self.assertEqual(headers["Authorization"], "Bearer tmp_test_token")

    async def test_start_message_omits_default_standard_operating_point(self) -> None:
        config = SpeechmaticsConfig(
            api_key="sk_test_1234567890",
            language="eo",
            operating_point="standard",
        )
        backend = SpeechmaticsRealtimeBackend(config)
        websocket = _FakeWebsocket()
        backend._websocket = websocket

        await backend._send_start_message()

        self.assertEqual(len(websocket.sent_messages), 1)
        payload = json.loads(websocket.sent_messages[0])
        self.assertEqual(payload["message"], "StartRecognition")
        self.assertEqual(payload["transcription_config"]["language"], "eo")
        self.assertNotIn("operating_point", payload["transcription_config"])

    async def test_start_message_includes_enhanced_operating_point(self) -> None:
        config = SpeechmaticsConfig(
            api_key="sk_test_1234567890",
            language="eo",
            operating_point="enhanced",
        )
        backend = SpeechmaticsRealtimeBackend(config)
        websocket = _FakeWebsocket()
        backend._websocket = websocket

        await backend._send_start_message()

        self.assertEqual(len(websocket.sent_messages), 1)
        payload = json.loads(websocket.sent_messages[0])
        self.assertEqual(payload["message"], "StartRecognition")
        self.assertEqual(payload["transcription_config"]["language"], "eo")
        self.assertEqual(payload["transcription_config"]["operating_point"], "enhanced")

    async def test_timelimit_close_message_is_actionable_and_not_retryable(self) -> None:
        config = SpeechmaticsConfig(
            api_key="sk_test_1234567890",
            language="eo",
            operating_point="enhanced",
        )
        backend = SpeechmaticsRealtimeBackend(config)
        close = Close(4006, "timelimit_exceeded")
        exc = ConnectionClosedError(close, close, True)

        error = backend._connection_closed_error(
            exc,
            "Speechmatics closed the connection while starting recognition",
        )

        message = str(error)
        self.assertIn("timelimit_exceeded", message)
        self.assertIn("usage time quota", message)
        self.assertIn("before audio was processed", message)
        self.assertIn("API key", message)
        self.assertIn("workspace", message)
        self.assertFalse(error.retryable)

    async def test_timelimit_server_error_message_is_actionable_and_not_retryable(self) -> None:
        backend = SpeechmaticsRealtimeBackend(
            SpeechmaticsConfig(api_key="sk_test_1234567890", language="eo")
        )

        error = backend._server_error_message(
            {
                "message": "Error",
                "type": "timelimit_exceeded",
                "reason": "Audio Usage Exceeded",
            }
        )

        message = str(error)
        self.assertIn("timelimit_exceeded", message)
        self.assertIn("Audio Usage Exceeded", message)
        self.assertIn("usage time quota", message)
        self.assertIn("before audio was processed", message)
        self.assertFalse(error.retryable)

    async def test_listener_failure_closes_websocket_without_closed_attribute(self) -> None:
        backend = SpeechmaticsRealtimeBackend(
            SpeechmaticsConfig(api_key="sk_test_1234567890", language="eo")
        )
        websocket = _FakeWebsocket()
        backend._websocket = websocket
        error = backend._server_error_message(
            {
                "message": "Error",
                "type": "timelimit_exceeded",
                "reason": "Audio Usage Exceeded",
            }
        )

        await backend._handle_listener_failure(error)

        self.assertIs(backend._listener_error, error)
        self.assertEqual(websocket.close_calls, [(1011, "transcription_backend_error")])


class SpeechmaticsSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_recovers_once_after_retryable_failure(self) -> None:
        backend = _make_backend()
        attempts: list[bytes] = []
        recoveries: list[Exception] = []

        async def fake_send_once(chunk: bytes) -> None:
            attempts.append(chunk)
            if len(attempts) == 1:
                raise SpeechmaticsRealtimeError("network blip", retryable=True)

        async def fake_recover(reason: Exception) -> None:
            recoveries.append(reason)

        backend._send_chunk_once = fake_send_once  # type: ignore[method-assign]
        backend._recover_connection = fake_recover  # type: ignore[method-assign]

        await backend.send_audio_chunk(b"pcm")

        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(backend._consecutive_recoveries, 0)

    async def test_send_propagates_non_retryable_failure_without_recovery(self) -> None:
        backend = _make_backend()
        recoveries: list[Exception] = []

        async def fake_send_once(chunk: bytes) -> None:
            raise SpeechmaticsRealtimeError("quota gone", retryable=False)

        async def fake_recover(reason: Exception) -> None:
            recoveries.append(reason)

        backend._send_chunk_once = fake_send_once  # type: ignore[method-assign]
        backend._recover_connection = fake_recover  # type: ignore[method-assign]

        with self.assertRaises(SpeechmaticsRealtimeError) as ctx:
            await backend.send_audio_chunk(b"pcm")

        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(recoveries, [])

    async def test_send_gives_up_after_too_many_consecutive_recoveries(self) -> None:
        backend = _make_backend()
        backend._consecutive_recoveries = backend._MAX_CONSECUTIVE_RECOVERIES

        async def fake_send_once(chunk: bytes) -> None:
            raise SpeechmaticsRealtimeError("still down", retryable=True)

        backend._send_chunk_once = fake_send_once  # type: ignore[method-assign]

        with self.assertRaises(SpeechmaticsRealtimeError) as ctx:
            await backend.send_audio_chunk(b"pcm")

        self.assertFalse(ctx.exception.retryable)

    async def test_recover_connection_reconnects_even_when_state_looks_healthy(self) -> None:
        """'Recognition did not start in time' leaves ws/_connected looking fine;
        recovery must still tear down and reconnect."""

        backend = _make_backend()
        backend._websocket = _FakeWebsocket()
        backend._connected.set()
        calls: list[str] = []

        async def fake_close() -> None:
            calls.append("close")

        async def fake_connect() -> None:
            calls.append("connect")

        backend.close = fake_close  # type: ignore[method-assign]
        backend.connect = fake_connect  # type: ignore[method-assign]

        await backend._recover_connection(SpeechmaticsRealtimeError("no RecognitionStarted"))

        self.assertEqual(calls, ["close", "connect"])

    async def test_reset_transcript_queue_preserves_pending_segments(self) -> None:
        backend = _make_backend()
        first = TranscriptSegment(text="Unua.", is_final=True)
        second = TranscriptSegment(text="Dua.", is_final=True)
        await backend._transcript_queue.put(first)
        await backend._transcript_queue.put(second)

        backend._reset_transcript_queue()

        preserved = []
        while not backend._transcript_queue.empty():
            preserved.append(backend._transcript_queue.get_nowait())
        self.assertEqual(preserved, [first, second])

    async def test_seq_no_counts_successful_audio_sends(self) -> None:
        backend = _make_backend()
        backend._websocket = _FakeWebsocket()
        backend._connected.set()
        backend._recognition_started.set()

        for _ in range(3):
            await backend.send_audio_chunk(b"pcm")

        self.assertEqual(backend._seq_no, 3)


class SpeechmaticsFinishStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_finish_stream_sends_end_of_stream_with_seq_no(self) -> None:
        backend = _make_backend()
        websocket = _FakeWebsocket()
        backend._websocket = websocket
        backend._connected.set()
        backend._seq_no = 7
        backend._end_of_transcript.set()

        await backend.finish_stream(timeout=1.0)

        payload = json.loads(websocket.sent_messages[-1])
        self.assertEqual(payload, {"message": "EndOfStream", "last_seq_no": 7})

    async def test_finish_stream_signals_end_when_disconnected(self) -> None:
        backend = _make_backend()

        await backend.finish_stream(timeout=1.0)

        collected = [item async for item in backend.transcript_results()]
        self.assertEqual(collected, [])


class SpeechmaticsTranscriptResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_end_cleanly_on_stream_ended_sentinel(self) -> None:
        backend = _make_backend()
        segment = TranscriptSegment(text="Saluton.", is_final=True)
        await backend._transcript_queue.put(segment)
        await backend._transcript_queue.put(_STREAM_ENDED)

        collected = [item async for item in backend.transcript_results()]

        self.assertEqual(collected, [segment])

    async def test_retryable_listener_error_does_not_end_results(self) -> None:
        backend = _make_backend()
        backend._listener_error = SpeechmaticsRealtimeError("blip", retryable=True)
        segment = TranscriptSegment(text="Daŭrigo.", is_final=True)
        await backend._transcript_queue.put(None)
        await backend._transcript_queue.put(segment)
        await backend._transcript_queue.put(_STREAM_ENDED)

        collected = [item async for item in backend.transcript_results()]

        self.assertEqual(collected, [segment])

    async def test_non_retryable_listener_error_raises(self) -> None:
        backend = _make_backend()
        backend._listener_error = SpeechmaticsRealtimeError("dead", retryable=False)
        await backend._transcript_queue.put(None)

        with self.assertRaises(SpeechmaticsRealtimeError):
            async for _item in backend.transcript_results():
                pass


if __name__ == "__main__":
    unittest.main()
