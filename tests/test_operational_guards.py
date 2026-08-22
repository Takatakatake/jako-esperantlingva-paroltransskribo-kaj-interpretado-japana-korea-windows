"""Tests for the operational guards: instance lock, WS origin check, and
default-source enforcement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from transcriber.audio_setup import AudioEnvironmentManager
from transcriber.cli import _acquire_instance_lock
from transcriber.config import AudioInputConfig
from transcriber.display.webui import CaptionWebUI


class InstanceLockTests(unittest.TestCase):
    """Uses an isolated lock path so tests never collide with a live session."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "test-instance.lock"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_acquisition_is_refused(self) -> None:
        first = _acquire_instance_lock(self.lock_path)
        self.assertIsNotNone(first)
        try:
            with self.assertRaises(SystemExit):
                _acquire_instance_lock(self.lock_path)
        finally:
            first.close()

    def test_lock_is_released_on_close(self) -> None:
        first = _acquire_instance_lock(self.lock_path)
        first.close()
        second = _acquire_instance_lock(self.lock_path)
        self.assertIsNotNone(second)
        second.close()


class _FakeRequest:
    def __init__(self, origin) -> None:
        self.headers = {} if origin is None else {"Origin": origin}


class OriginValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = CaptionWebUI(host="127.0.0.1", port=8765)

    def test_same_origin_allowed(self) -> None:
        self.assertTrue(self.ui._origin_allowed(_FakeRequest("http://127.0.0.1:8765")))
        self.assertTrue(self.ui._origin_allowed(_FakeRequest("http://localhost:8765")))

    def test_missing_origin_allowed_for_non_browser_clients(self) -> None:
        self.assertTrue(self.ui._origin_allowed(_FakeRequest(None)))

    def test_foreign_origins_rejected(self) -> None:
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("https://evil.example")))
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("http://127.0.0.1:9999")))
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("null")))
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("file://")))

    def test_malformed_origin_port_is_rejected_not_crash(self) -> None:
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("http://127.0.0.1:notaport")))

    def test_wildcard_bind_checks_port_only(self) -> None:
        ui = CaptionWebUI(host="0.0.0.0", port=8765)
        self.assertTrue(ui._origin_allowed(_FakeRequest("http://192.168.1.20:8765")))
        self.assertFalse(ui._origin_allowed(_FakeRequest("http://192.168.1.20:80")))

    def test_port_hop_updates_allowed_origin(self) -> None:
        self.ui.port = 8766
        self.assertTrue(self.ui._origin_allowed(_FakeRequest("http://127.0.0.1:8766")))
        self.assertFalse(self.ui._origin_allowed(_FakeRequest("http://127.0.0.1:8765")))


class EnforceDefaultSourceTests(unittest.TestCase):
    def _manager(self) -> AudioEnvironmentManager:
        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"):
            manager = AudioEnvironmentManager(AudioInputConfig())
        manager._expected_source = "codex_transcribe.monitor"
        return manager

    def test_drift_is_corrected(self) -> None:
        manager = self._manager()
        with mock.patch.object(
            manager, "_get_linux_defaults", return_value=("sink", "bluez_input.headset")
        ), mock.patch.object(
            manager,
            "_list_pactl_names",
            return_value=["codex_transcribe.monitor", "bluez_input.headset"],
        ), mock.patch("transcriber.audio_setup.subprocess.run") as mock_run:
            self.assertTrue(manager.enforce_default_source())

        mock_run.assert_called_once_with(
            ["pactl", "set-default-source", "codex_transcribe.monitor"],
            check=False,
            timeout=5,
        )

    def test_matching_source_is_noop(self) -> None:
        manager = self._manager()
        with mock.patch.object(
            manager, "_get_linux_defaults", return_value=("sink", "codex_transcribe.monitor")
        ), mock.patch("transcriber.audio_setup.subprocess.run") as mock_run:
            self.assertFalse(manager.enforce_default_source())

        mock_run.assert_not_called()

    def test_missing_monitor_is_not_enforced(self) -> None:
        manager = self._manager()
        with mock.patch.object(
            manager, "_get_linux_defaults", return_value=("sink", "bluez_input.headset")
        ), mock.patch.object(
            manager, "_list_pactl_names", return_value=["bluez_input.headset"]
        ), mock.patch("transcriber.audio_setup.subprocess.run") as mock_run:
            self.assertFalse(manager.enforce_default_source())

        mock_run.assert_not_called()

    def test_disabled_without_expected_source(self) -> None:
        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"):
            manager = AudioEnvironmentManager(AudioInputConfig())
        self.assertFalse(manager.enforce_default_source())

    def test_cleanup_disarms_enforcement(self) -> None:
        """A watchdog tick racing shutdown must not re-pin after cleanup."""

        manager = self._manager()
        manager.cleanup()

        with mock.patch("transcriber.audio_setup.subprocess.run") as mock_run:
            self.assertFalse(manager.enforce_default_source())

        mock_run.assert_not_called()


class UncleanLeftoverRestoreTests(unittest.TestCase):
    def test_physical_restore_prefers_alsa_devices(self) -> None:
        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"):
            manager = AudioEnvironmentManager(AudioInputConfig())

        def fake_list(kind: str):
            if kind == "sinks":
                return ["codex_transcribe", "hdmi_output.fake", "alsa_output.pci.analog"]
            return [
                "codex_transcribe.monitor",
                "alsa_output.pci.analog.monitor",
                "alsa_input.pci.analog",
            ]

        with mock.patch.object(manager, "_list_pactl_names", side_effect=fake_list), mock.patch(
            "transcriber.audio_setup.subprocess.run"
        ) as mock_run:
            manager._restore_physical_defaults()

        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn(["pactl", "set-default-sink", "alsa_output.pci.analog"], calls)
        self.assertIn(["pactl", "set-default-source", "alsa_input.pci.analog"], calls)

    def test_mixed_leftover_registers_per_field_repair(self) -> None:
        """Sink already fixed by the user but source still on the monitor:
        cleanup must restore the captured sink AND repair the source."""

        with mock.patch("transcriber.audio_setup.platform.system", return_value="Linux"), mock.patch(
            "transcriber.audio_setup.shutil.which", return_value="/usr/bin/pactl"
        ):
            manager = AudioEnvironmentManager(AudioInputConfig())

        pactl_calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            if cmd[0] == "pactl":
                pactl_calls.append(list(cmd))
            return mock.Mock(returncode=0)

        def fake_list(kind: str):
            if kind == "sinks":
                return ["codex_transcribe", "alsa_output.pci.analog"]
            return ["codex_transcribe.monitor", "alsa_input.pci.analog"]

        import os

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDIO_LOOPBACK_ALREADY_SET", None)
            with mock.patch.object(
                manager,
                "_get_linux_defaults",
                return_value=("alsa_output.pci.analog", "codex_transcribe.monitor"),
            ), mock.patch.object(manager, "_list_pactl_names", side_effect=fake_list), mock.patch(
                "transcriber.audio_setup.subprocess.run", side_effect=fake_run
            ), mock.patch.object(
                manager, "_detect_loopback_candidate", return_value=True
            ), mock.patch(
                "transcriber.audio_setup.Path.is_file", return_value=False
            ):
                manager._prepare_linux_loopback()
                manager.cleanup()

        self.assertIn(["pactl", "set-default-sink", "alsa_output.pci.analog"], pactl_calls)
        self.assertIn(["pactl", "set-default-source", "alsa_input.pci.analog"], pactl_calls)
        self.assertNotIn(
            ["pactl", "set-default-source", "codex_transcribe.monitor"], pactl_calls
        )


if __name__ == "__main__":
    unittest.main()
