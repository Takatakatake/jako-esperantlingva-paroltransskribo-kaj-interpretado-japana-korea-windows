"""Command line interface for the realtime transcription pipeline."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .env_check import run_environment_check
from .setup_wizard import run_setup_wizard


BACKEND_CHOICES = ("speechmatics", "vosk", "whisper")


def configure_stdio() -> None:
    """Avoid Windows codepage crashes for multilingual console output."""

    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def load_settings():
    """Lazy wrapper so environment checks can run before dependencies are installed."""

    from .config import load_settings as _load_settings

    return _load_settings()


def run_cli_diagnostics(audio_config) -> None:  # noqa: ANN001
    """Lazy wrapper for audio diagnostics, which require sounddevice."""

    from .audio_setup import run_cli_diagnostics as _run_cli_diagnostics

    _run_cli_diagnostics(audio_config)


def show_audio_routing_guide(audio_config) -> None:  # noqa: ANN001
    """Print a concise guide for microphone/loopback routing."""

    from .audio_setup import collect_audio_diagnostics, render_routing_guide

    print(render_routing_guide(collect_audio_diagnostics(audio_config)))


def show_audio_profile(profile: str) -> None:
    """Print a non-destructive audio profile plan."""

    from .audio_profiles import build_profile_plan, render_profile_plan

    plan = build_profile_plan(profile)
    print(render_profile_plan(plan))


def apply_audio_profile(profile: str) -> None:
    """Apply a safe audio capture profile when .env values need changing."""

    from .audio_profiles import apply_profile_to_env, render_profile_plan

    plan, backup_path = apply_profile_to_env(profile)
    print(render_profile_plan(plan, applied_backup=backup_path, unchanged=backup_path is None))


def list_env_backups() -> None:
    from .audio_profiles import list_env_backups as _list_backups
    from .audio_profiles import render_env_backups

    print(render_env_backups(_list_backups()))


def restore_env_backup(selector: str) -> None:
    from .audio_profiles import restore_env_backup as _restore_backup

    restored_from, safety_backup = _restore_backup(selector)
    print(f"Restored .env from: {restored_from}")
    print(f"Previous .env was saved as: {safety_backup}")


def set_speechmatics_operating_point(value: str) -> None:
    from .audio_profiles import apply_speechmatics_operating_point

    backup_path = apply_speechmatics_operating_point(value)
    if backup_path is None:
        print(f"SPEECHMATICS_OPERATING_POINT already set to: {value}")
        print(".env already had this value; no backup was created.")
        return
    print(f"SPEECHMATICS_OPERATING_POINT set to: {value}")
    print(f".env backup: {backup_path}")
    print(f"Restore this change: python -m transcriber.cli --restore-env-backup {backup_path.name}")


def set_speechmatics_auth_mode(value: str) -> None:
    from .audio_profiles import apply_speechmatics_auth_mode

    backup_path = apply_speechmatics_auth_mode(value)
    if backup_path is None:
        print(f"SPEECHMATICS_AUTH_MODE already set to: {value}")
        print(".env already had this value; no backup was created.")
        return
    print(f"SPEECHMATICS_AUTH_MODE set to: {value}")
    print(f".env backup: {backup_path}")
    print(f"Restore this change: python -m transcriber.cli --restore-env-backup {backup_path.name}")


def test_audio_levels(seconds: float, profile: str) -> None:
    """Run a short capture test without changing files or OS settings."""

    from .audio_probe import probe_audio_levels, render_audio_probe_result

    settings = load_settings()
    device_index = None
    device_sample_rate = None
    display_profile = profile

    if profile != "current":
        from .audio_profiles import build_profile_plan

        plan = build_profile_plan(profile)
        if plan.device is None:
            print("\n".join(plan.warnings) or f"No suitable device for profile: {profile}")
            return
        device_index = plan.device.index
        if plan.device.default_samplerate:
            device_sample_rate = int(round(plan.device.default_samplerate))

    result = probe_audio_levels(
        settings.audio,
        duration_seconds=seconds,
        device_index=device_index,
        device_sample_rate=device_sample_rate,
        channels=1,
    )
    print(render_audio_probe_result(result, profile=display_profile))


def open_audio_settings(target: str) -> None:
    """Open common audio settings panels when available."""

    system = platform.system().lower()
    if system == "linux":
        for command in (
            ["gnome-control-center", "sound"],
            ["pavucontrol"],
        ):
            executable = shutil.which(command[0])
            if executable:
                subprocess.Popen([executable, *command[1:]])  # noqa: S603
                return
        print("Audio settings app not found. Try installing pavucontrol or open your desktop sound settings.")
        return

    powershell = _find_windows_powershell()
    if not powershell:
        print("Windows PowerShell was not found; cannot open Windows audio settings automatically.")
        return

    commands = {
        "sound": ["Start-Process 'ms-settings:sound'"],
        "mixer": ["Start-Process 'ms-settings:apps-volume'"],
        "playback": ["Start-Process control.exe -ArgumentList 'mmsys.cpl,,0'"],
        "recording": ["Start-Process control.exe -ArgumentList 'mmsys.cpl,,1'"],
    }
    selected = [part for name in commands for part in commands[name]] if target == "all" else commands[target]
    subprocess.run([powershell, "-NoProfile", "-Command", "; ".join(selected)], check=False)


def _find_windows_powershell() -> Optional[str]:
    candidates = ["powershell.exe", "powershell", "pwsh.exe", "pwsh"]
    if platform.system().lower() == "windows":
        return next((path for name in candidates if (path := shutil.which(name))), "powershell")
    return next((path for name in ("powershell.exe", "pwsh.exe") if (path := shutil.which(name))), None)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def list_audio_devices() -> None:
    try:
        import sounddevice as sd
    except ImportError:
        print(
            "sounddevice is not installed. Activate .venv311 and run "
            "`python -m pip install -r requirements.txt`.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    devices = sd.query_devices()
    for index, device in enumerate(devices):
        io_type = []
        if device["max_input_channels"]:
            io_type.append("IN")
        if device["max_output_channels"]:
            io_type.append("OUT")
        print(f"{index:>3}: {'/'.join(io_type):<7} {device['name']}  ({device['hostapi']})")


def print_settings() -> None:
    settings = load_settings()
    filtered: Dict[str, Any] = {
        "backend": settings.backend.value,
        "audio": settings.audio.model_dump(),
        "zoom": settings.zoom.model_dump(),
        "logging": settings.logging.model_dump(),
        "web": settings.web.model_dump(),
        "discord": settings.discord.model_dump(),
    }
    if settings.speechmatics:
        filtered["speechmatics"] = {
            **settings.speechmatics.model_dump(),
            "api_key": "***redacted***",
        }
    if settings.vosk:
        filtered["vosk"] = settings.vosk.model_dump()
    if settings.whisper:
        filtered["whisper"] = settings.whisper.model_dump()
    translation_dump = settings.translation.model_dump()
    if translation_dump.get("libre_api_key"):
        translation_dump["libre_api_key"] = "***redacted***"
    filtered["translation"] = translation_dump
    print(json.dumps(filtered, indent=2, ensure_ascii=False))


def _capture_linux_defaults() -> Optional[tuple[Optional[str], Optional[str]]]:
    try:
        output = subprocess.check_output(
            ["pactl", "info"],
            text=True,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except Exception:  # noqa: BLE001
        return None

    sink = None
    source = None
    for line in output.splitlines():
        if line.startswith("Default Sink:"):
            sink = line.split(":", 1)[1].strip() or None
        elif line.startswith("Default Source:"):
            source = line.split(":", 1)[1].strip() or None
    # A leftover virtual sink from an unclean exit must never become the
    # restore target or the HEADPHONE_SINK hint.
    if sink and sink.startswith("codex_transcribe"):
        sink = None
    if source and source.startswith("codex_transcribe"):
        source = None
    if sink or source:
        return sink, source
    return None


def _restore_linux_defaults(defaults: Optional[tuple[Optional[str], Optional[str]]]) -> None:
    if not defaults:
        return
    sink, source = defaults
    if sink:
        subprocess.run(["pactl", "set-default-sink", sink], check=False)
    if source:
        subprocess.run(["pactl", "set-default-source", source], check=False)


def _list_linux_devices(kind: str) -> List[str]:
    assert kind in {"sinks", "sources"}
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", kind], text=True, stderr=subprocess.DEVNULL
        )
    except Exception:  # noqa: BLE001
        return []
    names: List[str] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.append(parts[1].strip())
    return names


def _ensure_linux_physical_defaults() -> None:
    try:
        info = subprocess.check_output(
            ["pactl", "info"],
            text=True,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except Exception:  # noqa: BLE001
        return

    current_sink: Optional[str] = None
    current_source: Optional[str] = None
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            current_sink = line.split(":", 1)[1].strip() or None
        elif line.startswith("Default Source:"):
            current_source = line.split(":", 1)[1].strip() or None

    sinks = _list_linux_devices("sinks")
    sources = _list_linux_devices("sources")

    physical_sinks = [n for n in sinks if "codex_transcribe" not in n]
    physical_sources = [
        n for n in sources if ".monitor" not in n and "codex_transcribe" not in n
    ]

    if current_sink and "codex_transcribe" in current_sink and physical_sinks:
        subprocess.run(["pactl", "set-default-sink", physical_sinks[0]], check=False)

    if current_source and (
        "codex_transcribe" in current_source or current_source.endswith(".monitor")
    ):
        if physical_sources:
            subprocess.run(
                ["pactl", "set-default-source", physical_sources[0]], check=False
            )


def _snapshot_linux_modules() -> Dict[str, Set[str]]:
    result: Dict[str, Set[str]] = {"null": set(), "loop": set()}
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "modules"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return result

    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        idx, name = parts[0].strip(), parts[1].strip()
        if name == "module-null-sink":
            result["null"].add(idx)
        elif name == "module-loopback":
            result["loop"].add(idx)
    return result


def _unload_linux_modules(modules: Dict[str, Set[str]]) -> None:
    for module_id in modules.get("loop", set()):
        subprocess.run(["pactl", "unload-module", module_id], check=False)
    for module_id in modules.get("null", set()):
        subprocess.run(["pactl", "unload-module", module_id], check=False)


def run_easy_start(
    backend_override: Optional[str] = None,
    log_file_override: Optional[str] = None,
    interactive: bool = True,
) -> bool:
    """Perform ready checks and optionally start the pipeline."""

    ready = run_environment_check()
    if not ready:
        print("環境チェックで問題が見つかりました。表示された項目を修正して再実行してください。")
        return False

    settings = load_settings()
    try:
        run_cli_diagnostics(settings.audio)
    except Exception as exc:
        if exc.__class__.__name__ != "AudioEnvironmentError":
            raise
        print(f"オーディオ環境エラー: {exc}")
        return False

    system = platform.system().lower()
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    script_path: Optional[Path] = None
    command: Optional[list[str]] = None
    defaults_before_script: Optional[tuple[Optional[str], Optional[str]]] = None
    modules_before_script: Optional[Dict[str, Set[str]]] = None
    modules_to_unload: Dict[str, Set[str]] = {"null": set(), "loop": set()}
    setup_ran = False

    if system == "linux":
        script_path = scripts_dir / "setup_audio_loopback_linux.sh"
        command = ["bash", str(script_path)]
        defaults_before_script = _capture_linux_defaults()
        modules_before_script = _snapshot_linux_modules()
    elif system == "darwin":
        script_path = scripts_dir / "setup_audio_loopback_macos.sh"
        command = ["bash", str(script_path)]
    elif system == "windows":
        script_path = scripts_dir / "setup_audio_loopback_windows.ps1"
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]

    stdin_is_tty = sys.stdin.isatty() if sys.stdin else False
    if interactive and not stdin_is_tty:
        logging.debug("Standard input is not a TTY; running easy-start in non-interactive mode.")
        interactive = False

    if not interactive:
        if defaults_before_script:
            _restore_linux_defaults(defaults_before_script)
        if system == "linux":
            _ensure_linux_physical_defaults()
        return True

    def prompt_yes_no(message: str, default: bool) -> bool:
        """Read a yes/no answer, falling back to default when input is unavailable."""
        try:
            response = input(message).strip().lower()
        except EOFError:
            logging.debug("Input unavailable for prompt %r; defaulting to %s.", message, default)
            return default
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        return default

    if script_path and script_path.exists() and command:
        run_setup = prompt_yes_no(
            f"{script_path.name} を実行してループバック設定を整えますか？ [y/N]: ",
            default=False,
        )
        if run_setup:
            try:
                subprocess.run(command, check=True)
                setup_ran = True
                if system == "linux":
                    modules_after = _snapshot_linux_modules()
                    if modules_before_script is None:
                        modules_before_script = {"null": set(), "loop": set()}
                    modules_to_unload["null"] = modules_after["null"] - modules_before_script.get("null", set())
                    modules_to_unload["loop"] = modules_after["loop"] - modules_before_script.get("loop", set())
            except Exception as exc:  # noqa: BLE001
                print(f"スクリプト実行中に問題が発生しました: {exc}")
                if defaults_before_script:
                    _restore_linux_defaults(defaults_before_script)
                if system == "linux":
                    modules_after = _snapshot_linux_modules()
                    if modules_before_script is None:
                        modules_before_script = {"null": set(), "loop": set()}
                    modules_to_unload["null"] = modules_after["null"] - modules_before_script.get("null", set())
                    modules_to_unload["loop"] = modules_after["loop"] - modules_before_script.get("loop", set())
                    _unload_linux_modules(modules_to_unload)
                return False
        else:
            defaults_before_script = None
            modules_before_script = None

    start_now = prompt_yes_no(
        "環境準備が整いました。文字起こしを今すぐ開始しますか？ [Y/n]: ",
        default=True,
    )
    headphone_env_previous: Optional[str] = None
    loopback_flag_previous: Optional[str] = None
    if start_now:
        try:
            if system == "linux" and setup_ran:
                # Only advertise a prepared loopback when the setup script
                # actually ran; otherwise the pipeline must do its own setup
                # instead of silently capturing the microphone.
                if defaults_before_script:
                    sink_hint = defaults_before_script[0]
                    if sink_hint:
                        headphone_env_previous = os.environ.get("HEADPHONE_SINK")
                        os.environ["HEADPHONE_SINK"] = sink_hint
                loopback_flag_previous = os.environ.get("AUDIO_LOOPBACK_ALREADY_SET")
                os.environ["AUDIO_LOOPBACK_ALREADY_SET"] = "1"
            run_pipeline_command(backend_override, log_file_override)
        finally:
            if defaults_before_script:
                _restore_linux_defaults(defaults_before_script)
            if system == "linux":
                if setup_ran:
                    if headphone_env_previous is not None:
                        os.environ["HEADPHONE_SINK"] = headphone_env_previous
                    else:
                        os.environ.pop("HEADPHONE_SINK", None)
                    if loopback_flag_previous is not None:
                        os.environ["AUDIO_LOOPBACK_ALREADY_SET"] = loopback_flag_previous
                    else:
                        os.environ.pop("AUDIO_LOOPBACK_ALREADY_SET", None)
                _unload_linux_modules(modules_to_unload)
                _ensure_linux_physical_defaults()
    else:
        print("パイプラインの起動をスキップしました。`python -m transcriber.cli --log-level=INFO` でいつでも開始できます。")
        if defaults_before_script:
            _restore_linux_defaults(defaults_before_script)
        if system == "linux":
            os.environ.pop("AUDIO_LOOPBACK_ALREADY_SET", None)
            os.environ.pop("HEADPHONE_SINK", None)
            _unload_linux_modules(modules_to_unload)
            _ensure_linux_physical_defaults()
    return True


async def run_pipeline(
    backend_override: Optional[str] = None, log_file_override: Optional[str] = None
) -> None:
    settings = load_settings()
    from .pipeline import TranscriptionPipeline

    pipeline = TranscriptionPipeline(
        settings,
        backend_override=backend_override,
        transcript_log_override=log_file_override,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    run_task: Optional[asyncio.Task] = None

    def handle_stop(*_args):
        if stop_event.is_set():
            logging.warning("Second stop signal received; forcing immediate shutdown.")
            if run_task is not None:
                run_task.cancel()
            return
        logging.info(
            "Received stop signal; finishing trailing transcripts "
            "(press Ctrl+C again to force quit)."
        )
        stop_event.set()

    def handle_suspend(*_args):
        logging.warning(
            "Ctrl+Z (suspend) detected; cleaning up instead of leaving audio in a loopback state."
        )
        handle_stop()

    def register_signal(sig: int, handler, label: str) -> None:
        try:
            loop.add_signal_handler(sig, handler)
            logging.debug("Registered %s using loop.add_signal_handler.", label)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(sig, lambda *_args: handler())
                logging.debug("Registered %s using signal.signal fallback.", label)
            except (ValueError, OSError, RuntimeError):
                logging.debug("Signal %s not supported on this platform.", label)

    register_signal(signal.SIGINT, handle_stop, "SIGINT")
    register_signal(signal.SIGTERM, handle_stop, "SIGTERM")
    if hasattr(signal, "SIGTSTP"):
        register_signal(signal.SIGTSTP, handle_suspend, "SIGTSTP")

    run_task = asyncio.create_task(pipeline.run())
    stop_task = asyncio.create_task(stop_event.wait())
    done, _pending = await asyncio.wait(
        {run_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_task in done and run_task not in done:
        pipeline.request_stop()
        try:
            await asyncio.wait_for(run_task, timeout=30.0)
        except asyncio.TimeoutError:
            logging.warning("Graceful shutdown timed out; cancelling the pipeline.")
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
        except asyncio.CancelledError:
            logging.info("Pipeline task cancelled.")
        except Exception as exc:  # noqa: BLE001
            logging.warning("Pipeline ended with an error during shutdown: %s", exc)
    else:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        await run_task


def run_pipeline_command(
    backend_override: Optional[str] = None, log_file_override: Optional[str] = None
) -> None:
    """Run the pipeline and report expected runtime failures without a traceback."""

    try:
        asyncio.run(run_pipeline(backend_override, log_file_override))
    except Exception as exc:
        from .asr.base import TranscriptionBackendError
        from .audio import AudioCaptureError

        if isinstance(exc, (AudioCaptureError, TranscriptionBackendError)):
            print(f"Transcription failed: {exc}")
            raise SystemExit(1) from None
        raise


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Realtime Esperanto transcription using Speechmatics and Zoom captions."
    )
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit.")
    parser.add_argument(
        "--show-config", action="store_true", help="Print loaded configuration and exit."
    )
    parser.add_argument(
        "--diagnose-audio",
        action="store_true",
        help="Run audio environment diagnostics and exit.",
    )
    parser.add_argument(
        "--audio-routing-guide",
        action="store_true",
        help="Show a concise microphone/loopback routing guide for the current devices.",
    )
    parser.add_argument(
        "--audio-profile",
        choices=("microphone", "loopback"),
        help="Preview a safe .env audio profile without changing files.",
    )
    parser.add_argument(
        "--apply-audio-profile",
        choices=("microphone", "loopback"),
        help="Safely apply a microphone or loopback audio profile, backing up .env only when values change.",
    )
    parser.add_argument(
        "--list-env-backups",
        action="store_true",
        help="List .env backups created by audio profile or Speechmatics mode changes.",
    )
    parser.add_argument(
        "--restore-env-backup",
        help="Restore .env from a backup path, or use 'latest'. Saves current .env first.",
    )
    parser.add_argument(
        "--set-speechmatics-operating-point",
        choices=("standard", "enhanced"),
        help="Safely switch Speechmatics realtime accuracy mode, backing up .env only when values change.",
    )
    parser.add_argument(
        "--set-speechmatics-auth-mode",
        choices=("temporary_key", "api_key"),
        help="Safely switch Speechmatics auth mode, backing up .env only when values change.",
    )
    parser.add_argument(
        "--test-audio-levels",
        nargs="?",
        const=3.0,
        type=float,
        metavar="SECONDS",
        help="Record a short input sample and report whether audio is present.",
    )
    parser.add_argument(
        "--test-audio-profile",
        choices=("current", "microphone", "loopback"),
        default="current",
        help="Device/profile to use with --test-audio-levels without changing .env.",
    )
    parser.add_argument(
        "--open-audio-settings",
        nargs="?",
        const="all",
        choices=("all", "sound", "mixer", "playback", "recording"),
        help="Open audio settings panels without changing settings.",
    )
    parser.add_argument(
        "--check-environment",
        action="store_true",
        help="Run dependency/configuration readiness checks and exit.",
    )
    parser.add_argument(
        "--setup-wizard",
        action="store_true",
        help="Show guided setup steps tailored to the detected OS.",
    )
    parser.add_argument(
        "--easy-start",
        action="store_true",
        help="Run environment checks and optionally launch transcription with minimal prompts.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        help="Override transcription backend selection.",
    )
    parser.add_argument(
        "--log-file",
        help="Override transcript log file output path.",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)

    if args.list_devices:
        list_audio_devices()
        return

    if args.show_config:
        print_settings()
        return

    if args.diagnose_audio:
        try:
            run_cli_diagnostics(load_settings().audio)
        except Exception as exc:
            if exc.__class__.__name__ != "AudioEnvironmentError":
                raise
            print(f"オーディオ環境エラー: {exc}")
        return

    if args.audio_routing_guide:
        try:
            show_audio_routing_guide(load_settings().audio)
        except Exception as exc:
            if exc.__class__.__name__ != "AudioEnvironmentError":
                raise
            print(f"オーディオルーティングガイドエラー: {exc}")
        return

    if args.audio_profile:
        show_audio_profile(args.audio_profile)
        return

    if args.apply_audio_profile:
        apply_audio_profile(args.apply_audio_profile)
        return

    if args.list_env_backups:
        list_env_backups()
        return

    if args.restore_env_backup:
        restore_env_backup(args.restore_env_backup)
        return

    if args.set_speechmatics_operating_point:
        set_speechmatics_operating_point(args.set_speechmatics_operating_point)
        return

    if args.set_speechmatics_auth_mode:
        set_speechmatics_auth_mode(args.set_speechmatics_auth_mode)
        return

    if args.test_audio_levels is not None:
        try:
            test_audio_levels(args.test_audio_levels, args.test_audio_profile)
        except Exception as exc:
            if exc.__class__.__name__ != "AudioProbeError":
                raise
            print(f"オーディオレベルテストエラー: {exc}")
        return

    if args.open_audio_settings:
        open_audio_settings(args.open_audio_settings)
        return

    if args.check_environment:
        ready = run_environment_check()
        if not ready:
            sys.exit(1)
        return

    if args.setup_wizard:
        run_setup_wizard()
        return

    if args.easy_start:
        if not run_easy_start(args.backend, args.log_file):
            raise SystemExit(1)
        return

    run_pipeline_command(args.backend, args.log_file)


if __name__ == "__main__":
    main()
