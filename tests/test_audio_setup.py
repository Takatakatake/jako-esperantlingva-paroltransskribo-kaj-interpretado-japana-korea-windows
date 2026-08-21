"""Unit tests for audio environment helpers."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import subprocess

from transcriber.audio_setup import (
    AudioDeviceSummary,
    AudioDiagnosticReport,
    AudioEnvironmentError,
    AudioEnvironmentManager,
    _sanitize_restore_defaults,
    collect_audio_diagnostics,
    render_routing_guide,
)
from transcriber.config import AudioCaptureMode, AudioInputConfig


class AudioSetupTests(unittest.TestCase):
    """Verify detection heuristics and guard rails."""

    @mock.patch("transcriber.audio_setup.sd.query_devices", return_value=[])
    def test_ensure_device_presence_without_inputs(self, mock_query_devices) -> None:
        manager = AudioEnvironmentManager(AudioInputConfig())
        with self.assertRaises(AudioEnvironmentError):
            manager._ensure_device_presence()
        mock_query_devices.assert_called()

    @mock.patch("transcriber.audio_setup.platform.system", return_value="Linux")
    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[
            {
                "name": "pipewire",
                "max_input_channels": 64,
                "max_output_channels": 64,
                "hostapi": 0,
            },
            {
                "name": "default",
                "max_input_channels": 64,
                "max_output_channels": 64,
                "hostapi": 0,
            },
        ],
    )
    def test_linux_pipewire_detected_as_loopback(self, mock_query_devices, mock_system) -> None:
        config = AudioInputConfig()
        report = collect_audio_diagnostics(config)
        self.assertTrue(report.loopback_candidates, "PipeWire/default monitors should be treated as candidates")
        self.assertFalse(report.issues, "No issues expected when monitors exist")
        mock_query_devices.assert_called()
        mock_system.assert_called()

    @mock.patch("transcriber.audio_setup.platform.system", return_value="Linux")
    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[
            {
                "name": "alsa_input.usb-Logitech_Webcam-00",
                "max_input_channels": 2,
                "max_output_channels": 0,
                "hostapi": 0,
            },
        ],
    )
    def test_collect_diagnostics_warns_when_no_loopback(self, mock_query_devices, mock_system) -> None:
        config = AudioInputConfig()
        diagnostics = collect_audio_diagnostics(config)
        self.assertIn(
            "ループバック入力候補が検出できませんでした。仮想デバイスやモニターを準備してください。",
            diagnostics.issues,
        )
        mock_query_devices.assert_called()
        mock_system.assert_called()

    @mock.patch("transcriber.audio_setup.subprocess.run")
    @mock.patch(
        "transcriber.audio_setup.AudioEnvironmentManager._detect_loopback_candidate",
        return_value=True,
    )
    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[
            {
                "name": "pipewire",
                "max_input_channels": 2,
                "max_output_channels": 2,
                "hostapi": 0,
            }
        ],
    )
    @mock.patch("transcriber.audio_setup.platform.system", return_value="Linux")
    def test_loopback_setup_skipped_when_flag_set(
        self,
        mock_system,
        mock_query_devices,
        mock_detect,
        mock_subprocess_run,
    ) -> None:
        config = AudioInputConfig()
        manager = AudioEnvironmentManager(config)
        with mock.patch.dict(
            os.environ, {"AUDIO_LOOPBACK_ALREADY_SET": "1"}, clear=False
        ), mock.patch.object(
            manager, "_get_linux_defaults", return_value=(None, "codex_transcribe.monitor")
        ):
            manager._prepare_loopback()
        # Verification may inspect pactl, but the setup script must not run.
        for call in mock_subprocess_run.call_args_list:
            self.assertNotEqual(call.args[0][0], "bash")

    def test_routing_guide_explains_linux_virtual_sink_flow(self) -> None:
        report = AudioDiagnosticReport(
            platform="Linux",
            mode=AudioCaptureMode.LOOPBACK,
            input_devices=[
                AudioDeviceSummary(4, "pipewire", "ALSA", 64, 64, 48000),
                AudioDeviceSummary(8, "codex_transcribe.monitor", "PulseAudio", 2, 0, 48000),
            ],
            output_devices=[
                AudioDeviceSummary(
                    3,
                    "alsa_output.pci-0000_00_1f.3.analog-stereo",
                    "PulseAudio",
                    0,
                    2,
                    48000,
                ),
            ],
            loopback_candidates=[
                AudioDeviceSummary(8, "codex_transcribe.monitor", "PulseAudio", 2, 0, 48000),
            ],
            configured_device=AudioDeviceSummary(
                8, "codex_transcribe.monitor", "PulseAudio", 2, 0, 48000
            ),
            default_input_device=AudioDeviceSummary(
                8, "codex_transcribe.monitor", "PulseAudio", 2, 0, 48000
            ),
            default_output_device=AudioDeviceSummary(
                3,
                "alsa_output.pci-0000_00_1f.3.analog-stereo",
                "PulseAudio",
                0,
                2,
                48000,
            ),
            issues=[],
            recommendations=[],
        )

        rendered = render_routing_guide(report)

        self.assertIn("codex_transcribe 仮想sink", rendered)
        self.assertIn("codex_transcribe.monitor", rendered)
        self.assertIn("module-loopback", rendered)
        self.assertIn("AUDIO_LINUX_LOOPBACK_SINK", rendered)


class SanitizeRestoreDefaultsTests(unittest.TestCase):
    def test_leftover_virtual_values_are_dropped(self) -> None:
        self.assertIsNone(
            _sanitize_restore_defaults(("codex_transcribe", "codex_transcribe.monitor"))
        )

    def test_physical_values_pass_through(self) -> None:
        self.assertEqual(
            _sanitize_restore_defaults(("alsa_output.analog", "alsa_input.analog")),
            ("alsa_output.analog", "alsa_input.analog"),
        )

    def test_mixed_values_keep_only_physical(self) -> None:
        self.assertEqual(
            _sanitize_restore_defaults(("codex_transcribe", "alsa_input.analog")),
            (None, "alsa_input.analog"),
        )


class PrepareFailureRollbackTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {}, clear=False)
    @mock.patch("transcriber.audio_setup.shutil.which", return_value="/usr/bin/pactl")
    @mock.patch(
        "transcriber.audio_setup.sd.query_devices",
        return_value=[{"name": "pipewire", "max_input_channels": 64, "max_output_channels": 64, "hostapi": 0}],
    )
    def test_prepare_failure_restores_previous_defaults(
        self, mock_query_devices, mock_which
    ) -> None:
        os.environ.pop("AUDIO_LOOPBACK_ALREADY_SET", None)
        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"):
            manager = AudioEnvironmentManager(AudioInputConfig())

        pactl_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            if cmd[0] == "bash":
                raise subprocess.CalledProcessError(1, cmd)
            pactl_calls.append(list(cmd))
            return mock.Mock(returncode=0)

        with mock.patch.object(
            manager, "_get_linux_defaults", return_value=("sinkA", "srcA")
        ), mock.patch("transcriber.audio_setup.subprocess.run", side_effect=fake_run):
            with self.assertRaises(AudioEnvironmentError):
                manager.prepare()

        self.assertIn(["pactl", "set-default-sink", "sinkA"], pactl_calls)
        self.assertIn(["pactl", "set-default-source", "srcA"], pactl_calls)


class AlreadySetVerificationTests(unittest.TestCase):
    def _manager(self) -> AudioEnvironmentManager:
        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"):
            return AudioEnvironmentManager(AudioInputConfig())

    @mock.patch("transcriber.audio_setup.shutil.which", return_value="/usr/bin/pactl")
    def test_flag_with_non_monitor_default_source_is_rejected(self, mock_which) -> None:
        manager = self._manager()
        with mock.patch.dict(os.environ, {"AUDIO_LOOPBACK_ALREADY_SET": "1"}), mock.patch.object(
            manager, "_get_linux_defaults", return_value=("sinkA", "alsa_input.analog")
        ):
            with self.assertRaises(AudioEnvironmentError):
                manager._prepare_linux_loopback()

    @mock.patch("transcriber.audio_setup.shutil.which", return_value="/usr/bin/pactl")
    def test_flag_with_monitor_default_source_is_accepted(self, mock_which) -> None:
        manager = self._manager()
        with mock.patch.dict(os.environ, {"AUDIO_LOOPBACK_ALREADY_SET": "1"}), mock.patch.object(
            manager, "_get_linux_defaults", return_value=("codex_transcribe", "codex_transcribe.monitor")
        ):
            manager._prepare_linux_loopback()


if __name__ == "__main__":
    unittest.main()
