"""Unit tests for environment readiness checks."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from transcriber.env_check import run_environment_check
from transcriber.cli import run_easy_start


class EnvCheckTests(unittest.TestCase):
    """Validate readiness checks with mocked dependencies."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    @mock.patch("transcriber.env_check.platform.system", return_value="Windows")
    @mock.patch("transcriber.env_check.importlib.util.find_spec", return_value=object())
    def test_ready_environment_windows(self, mock_find_spec, mock_system) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            os.chdir(tmp_path)

            (tmp_path / "requirements.txt").write_text("numpy>=1.26\n", encoding="utf-8")
            required = [
                "transcriber/__init__.py",
                "transcriber/cli.py",
                "transcriber/config.py",
                "transcriber/audio.py",
                "transcriber/pipeline.py",
                "transcriber/asr/speechmatics_backend.py",
            ]
            for rel in required:
                path = tmp_path / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# stub\n", encoding="utf-8")

            (tmp_path / "logs").mkdir()
            (tmp_path / "gen-lang-client-0219123936-d6e117f5a590.json").write_text("{}", encoding="utf-8")
            (tmp_path / ".env").write_text(
                "\n".join(
                    [
                        "SPEECHMATICS_API_KEY=sk_test_123",
                        "AUDIO_DEVICE_INDEX=4",
                        "GOOGLE_TRANSLATE_CREDENTIALS_PATH=gen-lang-client-0219123936-d6e117f5a590.json",
                    ]
                ),
                encoding="utf-8",
            )

            with redirect_stdout(io.StringIO()):
                ready = run_environment_check()

        self.assertTrue(ready)
        mock_find_spec.assert_called()
        mock_system.assert_called()

    @mock.patch("transcriber.env_check.platform.system", return_value="Windows")
    def test_missing_package_detected_windows(self, mock_system) -> None:
        def fake_find_spec(module_name: str):
            return None if module_name == "numpy" else object()

        with mock.patch("transcriber.env_check.importlib.util.find_spec", side_effect=fake_find_spec):
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                os.chdir(tmp_path)

                (tmp_path / "requirements.txt").write_text("numpy>=1.26\n", encoding="utf-8")
                (tmp_path / "transcriber").mkdir()
                (tmp_path / "transcriber/__init__.py").write_text("# stub\n", encoding="utf-8")
                (tmp_path / "logs").mkdir()
                (tmp_path / ".env").write_text("", encoding="utf-8")
                (tmp_path / "gen-lang-client-0219123936-d6e117f5a590.json").write_text("{}", encoding="utf-8")

                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    ready = run_environment_check()

        output = buffer.getvalue()
        self.assertFalse(ready)
        self.assertIn("numpy", output)
        mock_system.assert_called()

    @mock.patch("transcriber.cli.run_environment_check", return_value=True)
    @mock.patch("transcriber.cli.load_settings")
    @mock.patch("transcriber.cli.run_cli_diagnostics")
    def test_easy_start_non_interactive_windows(
        self,
        mock_run_cli_diagnostics,
        mock_load_settings,
        mock_run_env_check,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(audio="stub")
        result = run_easy_start(interactive=False)
        self.assertTrue(result)
        mock_run_env_check.assert_called_once()
        mock_run_cli_diagnostics.assert_called_once()

    @mock.patch("transcriber.cli.asyncio.run")
    @mock.patch("transcriber.cli.subprocess.run")
    @mock.patch("transcriber.cli.run_environment_check", return_value=True)
    @mock.patch("transcriber.cli.load_settings")
    @mock.patch("transcriber.cli.run_cli_diagnostics")
    def test_easy_start_interactive_windows(
        self,
        mock_run_cli_diagnostics,
        mock_load_settings,
        mock_run_env_check,
        mock_subprocess_run,
        mock_asyncio_run,
    ) -> None:
        mock_load_settings.return_value = SimpleNamespace(audio="stub")
        mock_asyncio_run.return_value = None
        with mock.patch("builtins.input", side_effect=["y", "y"]):
            result = run_easy_start()
        self.assertTrue(result)
        mock_run_env_check.assert_called_once()
        mock_run_cli_diagnostics.assert_called_once()
        mock_subprocess_run.assert_called_once()
        mock_asyncio_run.assert_called_once()
