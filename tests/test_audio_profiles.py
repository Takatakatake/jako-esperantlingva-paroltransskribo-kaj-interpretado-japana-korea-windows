"""Unit tests for safe audio profile helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from transcriber.audio_profiles import (
    DeviceChoice,
    ProfilePlan,
    _unique_env_sidecar_path,
    apply_env_updates_to_text,
    apply_profile_to_env,
    apply_speechmatics_auth_mode,
    apply_speechmatics_operating_point,
    choose_loopback,
    choose_microphone,
    list_env_backups,
    render_profile_plan,
    restore_env_backup,
)


class AudioProfileTests(unittest.TestCase):
    """Validate profile generation without touching OS audio settings."""

    def test_choose_microphone_prefers_physical_mic(self) -> None:
        devices = [
            DeviceChoice(1, "alsa_output.pci.monitor", "ALSA", 2, 0, 48000),
            DeviceChoice(2, "default", "ALSA", 2, 2, 48000),
            DeviceChoice(3, "マイク (USB Camera)", "PulseAudio", 2, 0, 16000),
        ]

        choice = choose_microphone(devices)

        self.assertIsNotNone(choice)
        self.assertEqual(choice.index, 3)

    def test_choose_loopback_prefers_monitor_input(self) -> None:
        devices = [
            DeviceChoice(4, "default", "ALSA", 32, 32, 48000),
            DeviceChoice(5, "alsa_output.pci-0000.monitor", "PulseAudio", 2, 0, 48000),
            DeviceChoice(6, "Microphone (USB Camera)", "PulseAudio", 2, 0, 16000),
        ]

        choice = choose_loopback(devices)

        self.assertIsNotNone(choice)
        self.assertEqual(choice.index, 5)

    def test_empty_device_list_does_not_query_real_audio_stack(self) -> None:
        self.assertIsNone(choose_microphone([]))
        self.assertIsNone(choose_loopback([]))

    def test_apply_env_updates_preserves_comments_and_updates_active_keys(self) -> None:
        original = "\n".join(
            [
                "# AUDIO_DEVICE_INDEX=24",
                "AUDIO_DEVICE_INDEX=19",
                "AUDIO_CAPTURE_MODE=microphone",
            ]
        )

        updated = apply_env_updates_to_text(
            original,
            {
                "AUDIO_DEVICE_INDEX": "20",
                "AUDIO_CAPTURE_MODE": "microphone",
                "AUDIO_AUTO_SETUP_LOOPBACK": "false",
            },
        )

        self.assertIn("# AUDIO_DEVICE_INDEX=24", updated)
        self.assertIn("AUDIO_DEVICE_INDEX=20", updated)
        self.assertIn("AUDIO_AUTO_SETUP_LOOPBACK=false", updated)

    def test_apply_profile_to_env_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SPEECHMATICS_API_KEY=sk_test_1234567890\nAUDIO_DEVICE_INDEX=1\n",
                encoding="utf-8",
            )

            plan_device = DeviceChoice(7, "マイク (USB Camera)", "PulseAudio", 2, 0, 16000)
            plan = ProfilePlan(
                profile="microphone",
                device=plan_device,
                updates={
                    "AUDIO_CAPTURE_MODE": "microphone",
                    "AUDIO_DEVICE_INDEX": "7",
                    "AUDIO_DEVICE_SAMPLE_RATE": "16000",
                },
                notes=[],
                warnings=[],
            )

            import transcriber.audio_profiles as audio_profiles

            with mock.patch.object(audio_profiles, "build_profile_plan", return_value=plan):
                _plan, backup_path = apply_profile_to_env("microphone", env_path=env_path)

            self.assertTrue(backup_path.exists())
            self.assertIn("AUDIO_DEVICE_INDEX=7", env_path.read_text(encoding="utf-8"))

    def test_apply_profile_to_env_is_noop_when_already_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "AUDIO_CAPTURE_MODE=loopback\n"
                "AUDIO_DEVICE_INDEX=24\n"
                "AUDIO_DEVICE_SAMPLE_RATE=16000\n",
                encoding="utf-8",
            )

            plan = ProfilePlan(
                profile="loopback",
                device=DeviceChoice(24, "alsa_output.monitor", "PulseAudio", 2, 0, 16000),
                updates={
                    "AUDIO_CAPTURE_MODE": "loopback",
                    "AUDIO_DEVICE_INDEX": "24",
                    "AUDIO_DEVICE_SAMPLE_RATE": "16000",
                },
                notes=[],
                warnings=[],
            )

            import transcriber.audio_profiles as audio_profiles

            with mock.patch.object(audio_profiles, "build_profile_plan", return_value=plan):
                returned_plan, backup_path = apply_profile_to_env("loopback", env_path=env_path)

            self.assertEqual(plan, returned_plan)
            self.assertIsNone(backup_path)
            self.assertEqual([], list(Path(tmpdir).glob(".env.bak.*")))
            rendered = render_profile_plan(returned_plan, unchanged=True)
            self.assertIn("no backup was created", rendered)
            self.assertIn("復元は不要", rendered)

    def test_apply_speechmatics_operating_point_updates_env_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SPEECHMATICS_API_KEY=sk_test_1234567890\n"
                "SPEECHMATICS_OPERATING_POINT=standard\n",
                encoding="utf-8",
            )

            backup_path = apply_speechmatics_operating_point("enhanced", env_path=env_path)

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertIn(
                "SPEECHMATICS_OPERATING_POINT=standard",
                backup_path.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "SPEECHMATICS_OPERATING_POINT=enhanced",
                env_path.read_text(encoding="utf-8"),
            )

    def test_apply_speechmatics_operating_point_is_noop_when_already_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SPEECHMATICS_API_KEY=sk_test_1234567890\n"
                "SPEECHMATICS_OPERATING_POINT=enhanced\n",
                encoding="utf-8",
            )

            backup_path = apply_speechmatics_operating_point("enhanced", env_path=env_path)

            self.assertIsNone(backup_path)
            self.assertEqual([], list(Path(tmpdir).glob(".env.bak.*")))

    def test_apply_speechmatics_auth_mode_updates_env_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SPEECHMATICS_API_KEY=sk_test_1234567890\n"
                "SPEECHMATICS_AUTH_MODE=temporary_key\n",
                encoding="utf-8",
            )

            backup_path = apply_speechmatics_auth_mode("api_key", env_path=env_path)

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertIn(
                "SPEECHMATICS_AUTH_MODE=temporary_key",
                backup_path.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "SPEECHMATICS_AUTH_MODE=api_key",
                env_path.read_text(encoding="utf-8"),
            )

    def test_apply_speechmatics_auth_mode_is_noop_when_alias_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "SPEECHMATICS_API_KEY=sk_test_1234567890\n"
                "SPEECHMATICS_AUTH_MODE=temporary_key\n",
                encoding="utf-8",
            )

            backup_path = apply_speechmatics_auth_mode("jwt", env_path=env_path)

            self.assertIsNone(backup_path)
            self.assertEqual([], list(Path(tmpdir).glob(".env.bak.*")))

    def test_apply_speechmatics_auth_mode_rejects_unknown_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SPEECHMATICS_API_KEY=sk_test_1234567890\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "auth_mode"):
                apply_speechmatics_auth_mode("magic", env_path=env_path)

    def test_list_and_restore_env_backup_preserves_current_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("AUDIO_DEVICE_INDEX=20\n", encoding="utf-8")
            old_backup = Path(tmpdir) / ".env.bak.20000101_000000"
            old_backup.write_text("AUDIO_DEVICE_INDEX=21\n", encoding="utf-8")

            backups = list_env_backups(env_path)
            restored_from, safety_backup = restore_env_backup("latest", env_path=env_path)

            self.assertEqual(backups[0], old_backup)
            self.assertEqual(restored_from, old_backup)
            self.assertTrue(safety_backup.exists())
            self.assertIn("AUDIO_DEVICE_INDEX=20", safety_backup.read_text(encoding="utf-8"))
            self.assertIn("AUDIO_DEVICE_INDEX=21", env_path.read_text(encoding="utf-8"))

    def test_unique_sidecar_path_never_overwrites_existing_backup(self) -> None:
        class FixedDateTime:
            @classmethod
            def now(cls):
                return cls()

            def strftime(self, _fmt: str) -> str:
                return "20260101_010203"

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            first = Path(tmpdir) / ".env.bak.20260101_010203"
            first.write_text("old\n", encoding="utf-8")

            with mock.patch("transcriber.audio_profiles.datetime", FixedDateTime):
                sidecar = _unique_env_sidecar_path(env_path, "bak")

        self.assertEqual(sidecar.name, ".env.bak.20260101_010203.01")


if __name__ == "__main__":
    unittest.main()
