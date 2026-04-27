"""Unit tests for the Speechmatics realtime backend."""

from __future__ import annotations

import json
import unittest

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from transcriber.asr.speechmatics_backend import SpeechmaticsRealtimeBackend
from transcriber.config import SpeechmaticsConfig


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


if __name__ == "__main__":
    unittest.main()
