"""Command line interface for the realtime transcription pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import sounddevice as sd

from .audio_setup import AudioEnvironmentError, run_cli_diagnostics
from .config import BackendChoice, load_settings
from .env_check import run_environment_check
from .pipeline import TranscriptionPipeline
from .setup_wizard import run_setup_wizard


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def list_audio_devices() -> None:
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
        output = subprocess.check_output(["pactl", "info"], text=True, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        return None

    sink = None
    source = None
    for line in output.splitlines():
        if line.startswith("Default Sink:"):
            sink = line.split(":", 1)[1].strip() or None
        elif line.startswith("Default Source:"):
            source = line.split(":", 1)[1].strip() or None
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
            ["pactl", "info"], text=True, stderr=subprocess.DEVNULL
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
        print("Environment check reported issues. Please resolve them and rerun.")
        return False

    settings = load_settings()
    try:
        run_cli_diagnostics(settings.audio)
    except AudioEnvironmentError as exc:
        print(f"Audio diagnostic error: {exc}")
        return False

    system = platform.system().lower()
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if system != "windows":
        print("The easy-start helper currently supports Windows only.")
        return False

    script_path = scripts_dir / "setup_audio_loopback_windows.ps1"
    command: list[str] = [
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

    if script_path.exists():
        run_setup = prompt_yes_no(
            f"Run {script_path.name} to configure loopback audio? [y/N]: ",
            default=False,
        )
        if run_setup:
            try:
                subprocess.run(command, check=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Problem while running helper script: {exc}")
                return False
        else:
            script_path = None

    default_start = interactive
    start_now = prompt_yes_no("Environment ready. Start transcription now? [Y/n]: ", default_start)
    if start_now:
        asyncio.run(run_pipeline(backend_override, log_file_override))
    else:
        print("Skipped launching the pipeline. Run `python -m transcriber.cli --log-level=INFO` to start it later.")
    return True


async def run_pipeline(
    backend_override: Optional[str] = None, log_file_override: Optional[str] = None
) -> None:
    settings = load_settings()
    pipeline = TranscriptionPipeline(
        settings,
        backend_override=backend_override,
        transcript_log_override=log_file_override,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_stop(*_args):
        logging.info("Received stop signal, shutting down.")
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
    await stop_event.wait()
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        logging.info("Pipeline task cancelled.")


def main() -> None:
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
        choices=[choice.value for choice in BackendChoice],
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
        except AudioEnvironmentError as exc:
            print(f"Audio diagnostic error: {exc}")
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
        run_easy_start(args.backend, args.log_file)
        return

    asyncio.run(run_pipeline(args.backend, args.log_file))


if __name__ == "__main__":
    main()
