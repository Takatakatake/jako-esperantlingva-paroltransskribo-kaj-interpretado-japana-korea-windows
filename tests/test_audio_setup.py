"""Unit tests for audio environment helpers."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from transcriber.audio_setup import (
    AudioEnvironmentError,
    AudioEnvironmentManager,
    collect_audio_diagnostics,
)
from transcriber.config import AudioInputConfig


class AudioSetupTests(unittest.TestCase):
    """Verify detection heuristics and guard rails."""

    @mock.patch("transcriber.audio_setup.sd.query_devices", return_value=[])
    def test_ensure_device_presence_without_inputs(self, mock_query_devices) -> None:
        manager = AudioEnvironmentManager(AudioInputConfig())
        with self.assertRaises(AudioEnvironmentError):
            manager._ensure_device_presence()
        mock_query_devices.assert_called()

    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[
            {
                "name": "CABLE Output (VB-Audio Virtual Cable)",
                "max_input_channels": 2,
                "max_output_channels": 0,
                "hostapi": 0,
            }
        ],
    )
    @mock.patch("transcriber.audio_setup.platform.system", return_value="Windows")
    def test_windows_virtual_cable_detected_as_loopback(self, mock_system, mock_query_devices) -> None:
        config = AudioInputConfig()
        report = collect_audio_diagnostics(config)
        self.assertTrue(report.loopback_candidates, "VB-Audio virtual cable should be treated as a candidate")
        mock_query_devices.assert_called()
        mock_system.assert_called()

    @mock.patch("transcriber.audio_setup.shutil.which", return_value="powershell")
    @mock.patch("transcriber.audio_setup.subprocess.run")
    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[{"name": "Integrated Microphone", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0}],
    )
    @mock.patch("transcriber.audio_setup.platform.system", return_value="Windows")
    def test_prepare_windows_loopback_raises_when_missing(
        self,
        mock_system,
        mock_query_devices,
        mock_subprocess_run,
        mock_which,
    ) -> None:
        config = AudioInputConfig(auto_setup_loopback=True, device_index=None)
        manager = AudioEnvironmentManager(config)
        with self.assertRaises(AudioEnvironmentError):
            manager._prepare_windows_loopback()
        mock_subprocess_run.assert_called()
        mock_system.assert_called()
        mock_query_devices.assert_called()


if __name__ == "__main__":
    unittest.main()
