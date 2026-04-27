"""Unit tests for configuration parsing."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from transcriber.config import load_settings


class ConfigTests(unittest.TestCase):
    """Validate tolerant parsing of environment-driven settings."""

    def tearDown(self) -> None:
        load_settings.cache_clear()

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_blank_optional_audio_values_are_treated_as_auto(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "AUDIO_DEVICE_INDEX": "",
            "AUDIO_DEVICE_SAMPLE_RATE": "",
            "AUDIO_BLOCKSIZE": "",
            "AUDIO_WINDOWS_LOOPBACK_DEVICE": "",
            "AUDIO_LINUX_LOOPBACK_SINK": "",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            settings = load_settings()

        self.assertIsNone(settings.audio.device_index)
        self.assertIsNone(settings.audio.device_sample_rate)
        self.assertIsNone(settings.audio.blocksize)
        self.assertIsNone(settings.audio.windows_loopback_device)
        self.assertIsNone(settings.audio.linux_loopback_sink)

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_invalid_optional_int_reports_setting_name(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "AUDIO_DEVICE_INDEX": "not-a-number",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "AUDIO_DEVICE_INDEX"):
                load_settings()

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_speechmatics_operating_point_is_normalized(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "SPEECHMATICS_AUTH_MODE": "DIRECT",
            "SPEECHMATICS_OPERATING_POINT": "ENHANCED",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            settings = load_settings()

        self.assertIsNotNone(settings.speechmatics)
        self.assertEqual(settings.speechmatics.auth_mode, "api_key")
        self.assertEqual(settings.speechmatics.operating_point, "enhanced")

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_speechmatics_auth_mode_default_preserves_temporary_key_flow(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            settings = load_settings()

        self.assertIsNotNone(settings.speechmatics)
        self.assertEqual(settings.speechmatics.auth_mode, "temporary_key")

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_speechmatics_auth_mode_temporary_alias_is_normalized(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "SPEECHMATICS_AUTH_MODE": "jwt",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            settings = load_settings()

        self.assertIsNotNone(settings.speechmatics)
        self.assertEqual(settings.speechmatics.auth_mode, "temporary_key")

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_invalid_speechmatics_operating_point_reports_setting_name(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "SPEECHMATICS_OPERATING_POINT": "maximum",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "SPEECHMATICS_OPERATING_POINT"):
                load_settings()

    @mock.patch("transcriber.config.load_dotenv", return_value=None)
    def test_invalid_speechmatics_auth_mode_reports_setting_name(self, _mock_load_dotenv) -> None:
        env = {
            "SPEECHMATICS_API_KEY": "sk_test_1234567890",
            "SPEECHMATICS_AUTH_MODE": "magic",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            load_settings.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "SPEECHMATICS_AUTH_MODE"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
