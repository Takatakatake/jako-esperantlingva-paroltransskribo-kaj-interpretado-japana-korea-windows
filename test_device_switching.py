#!/usr/bin/env python3
"""Test script for audio device hot-reload functionality."""

import asyncio
import logging
import sys

from transcriber.audio import AudioChunkStream
from transcriber.config import AudioInputConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)


async def test_device_monitoring():
    """Test audio device monitoring and reconnection."""

    config = AudioInputConfig(
        device_index=None,  # Use system default
        sample_rate=16000,
        channels=1,
        chunk_duration_seconds=0.5,
        device_check_interval=2.0,
    )

    stream = AudioChunkStream(config, check_interval=2.0)

    print("\n" + "=" * 60)
    print("Audio Device Hot-Reload Test")
    print("=" * 60)
    print("\nThis test will monitor audio device changes.")
    print("Try switching your audio output device in system settings.")
    print("The stream should automatically reconnect to the new device.")
    print("\nPress Ctrl+C to stop the test.\n")
    print("=" * 60 + "\n")

    chunk_count = 0

    try:
        async with stream.connect() as audio_stream:
            async for chunk in audio_stream:
                chunk_count += 1
                if chunk_count % 20 == 0:  # Log every ~10 seconds
                    print(f"Received {chunk_count} audio chunks ({len(chunk)} bytes each)")
                await asyncio.sleep(0.01)  # Small delay to reduce CPU usage
    except KeyboardInterrupt:
        print(f"\n\nTest stopped. Total chunks received: {chunk_count}")
    except Exception as exc:
        print(f"\n\nTest failed with error: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(test_device_monitoring())
