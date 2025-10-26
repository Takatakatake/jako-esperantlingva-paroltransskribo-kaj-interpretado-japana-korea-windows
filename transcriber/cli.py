"""Command line interface for the realtime transcription pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from typing import Any, Dict, Optional

import sounddevice as sd

from .config import BackendChoice, load_settings
from .pipeline import TranscriptionPipeline


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

    try:
        loop.add_signal_handler(signal.SIGINT, handle_stop)
        loop.add_signal_handler(signal.SIGTERM, handle_stop)
    except NotImplementedError:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handle_stop)
            except (ValueError, RuntimeError, AttributeError):
                continue

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

    asyncio.run(run_pipeline(args.backend, args.log_file))


if __name__ == "__main__":
    main()
