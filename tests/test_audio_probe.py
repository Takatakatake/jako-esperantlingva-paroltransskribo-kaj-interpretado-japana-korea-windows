"""Unit tests for short audio level probes."""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from transcriber.audio_probe import AudioProbeError, probe_audio_levels, render_audio_probe_result
from transcriber.config import AudioInputConfig


class AudioProbeTests(unittest.TestCase):
    """Verify audio probe math and guard rails without touching real devices."""

    @mock.patch("transcriber.audio_probe.sd.wait", return_value=None)
    @mock.patch("transcriber.audio_probe.sd.rec")
    @mock.patch(
        "transcriber.audio_probe.sd.query_devices",
        return_value={"name": "マイク (USB Camera)", "max_input_channels": 2},
    )
    def test_probe_detects_signal(self, mock_query_devices, mock_rec, mock_wait) -> None:
        data = np.full((1600, 1), 2000, dtype=np.int16)
        mock_rec.return_value = data

        result = probe_audio_levels(
            AudioInputConfig(device_index=20, sample_rate=16000, device_sample_rate=16000),
            duration_seconds=0.1,
        )

        self.assertFalse(result.silent)
        self.assertFalse(result.clipped)
        self.assertEqual(result.device_index, 20)
        self.assertIn("入力信号を検出", render_audio_probe_result(result))
        mock_query_devices.assert_called_once_with(20, kind="input")
        mock_rec.assert_called_once()
        mock_wait.assert_called_once()

    @mock.patch("transcriber.audio_probe.sd.wait", return_value=None)
    @mock.patch("transcriber.audio_probe.sd.rec", return_value=np.zeros((1600, 1), dtype=np.int16))
    @mock.patch(
        "transcriber.audio_probe.sd.query_devices",
        return_value={"name": "alsa_output.monitor", "max_input_channels": 2},
    )
    def test_probe_reports_silence(self, _mock_query_devices, _mock_rec, _mock_wait) -> None:
        result = probe_audio_levels(
            AudioInputConfig(device_index=21, sample_rate=16000, device_sample_rate=16000),
            duration_seconds=0.1,
        )

        self.assertTrue(result.silent)
        self.assertIn("ほぼ無音", render_audio_probe_result(result, profile="loopback"))

    @mock.patch(
        "transcriber.audio_probe.sd.query_devices",
        return_value={"name": "Speaker Output", "max_input_channels": 0},
    )
    def test_probe_rejects_non_input_device(self, _mock_query_devices) -> None:
        with self.assertRaises(AudioProbeError):
            probe_audio_levels(AudioInputConfig(device_index=5), duration_seconds=0.1)


if __name__ == "__main__":
    unittest.main()
