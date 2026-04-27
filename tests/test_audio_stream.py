"""Unit tests for resilient audio device selection."""

from __future__ import annotations

import unittest
from unittest import mock

from transcriber.audio import AudioChunkStream
from transcriber.config import AudioInputConfig


def _fake_query_devices(devices: list[dict], default_input: int = 0):
    def fake_query_devices(device=None, kind=None):  # noqa: ANN001
        if isinstance(device, int):
            if device < 0 or device >= len(devices):
                raise ValueError(f"Invalid device index: {device}")
            selected = {"index": device, **devices[device]}
            if kind == "input" and selected.get("max_input_channels", 0) <= 0:
                raise ValueError(f"Device {device} has no input channels")
            return selected

        if kind == "input":
            selected = {"index": default_input, **devices[default_input]}
            if selected.get("max_input_channels", 0) <= 0:
                raise ValueError("Default input has no input channels")
            return selected

        return devices

    return fake_query_devices


class AudioChunkStreamDeviceSelectionTests(unittest.TestCase):
    def test_named_loopback_device_wins_when_index_now_points_to_microphone(self) -> None:
        devices = [
            {
                "name": "Microphone (USB Camera)",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
            {
                "name": "Microphone (Realtek Audio)",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
            {
                "name": "alsa_output.pci-0000.monitor",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
        ]
        config = AudioInputConfig(
            device_index=1,
            linux_loopback_sink="alsa_output.pci-0000.monitor",
            device_sample_rate=16000,
        )
        stream = AudioChunkStream(config)

        with mock.patch("transcriber.audio.sd.query_devices", side_effect=_fake_query_devices(devices)):
            with mock.patch(
                "transcriber.audio.sd.query_hostapis",
                return_value=[{"name": "PulseAudio"}],
            ):
                self.assertEqual(stream._get_effective_device(), 2)

    def test_named_loopback_device_is_used_when_saved_index_disappears(self) -> None:
        devices = [
            {
                "name": "Microphone (USB Camera)",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
            {
                "name": "alsa_output.pci-0000.monitor",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
        ]
        config = AudioInputConfig(
            device_index=24,
            linux_loopback_sink="alsa_output.pci-0000.monitor",
            device_sample_rate=16000,
        )
        stream = AudioChunkStream(config)

        with mock.patch("transcriber.audio.sd.query_devices", side_effect=_fake_query_devices(devices)):
            with mock.patch(
                "transcriber.audio.sd.query_hostapis",
                return_value=[{"name": "PulseAudio"}],
            ):
                self.assertEqual(stream._get_effective_device(), 1)

    def test_configured_index_is_kept_without_preferred_name(self) -> None:
        devices = [
            {
                "name": "Microphone (USB Camera)",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
            {
                "name": "alsa_output.pci-0000.monitor",
                "hostapi": 0,
                "max_input_channels": 2,
                "default_samplerate": 16000,
            },
        ]
        config = AudioInputConfig(device_index=1, device_sample_rate=16000)
        stream = AudioChunkStream(config)

        with mock.patch("transcriber.audio.sd.query_devices", side_effect=_fake_query_devices(devices)):
            self.assertEqual(stream._get_effective_device(), 1)


if __name__ == "__main__":
    unittest.main()
