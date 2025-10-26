#!/usr/bin/env python3
"""Audio device diagnostic script for Ubuntu troubleshooting."""

import sys
import sounddevice as sd


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def main():
    """Run audio device diagnostics."""
    print_section("Audio Device Diagnostics")

    # List all audio devices
    print_section("All Available Audio Devices")
    devices = sd.query_devices()
    if isinstance(devices, list):
        for idx, device in enumerate(devices):
            device_type = []
            if device.get('max_input_channels', 0) > 0:
                device_type.append("INPUT")
            if device.get('max_output_channels', 0) > 0:
                device_type.append("OUTPUT")

            print(f"[{idx}] {device['name']}")
            print(f"    Type: {', '.join(device_type)}")
            print(f"    Channels: {device.get('max_input_channels', 0)} in, "
                  f"{device.get('max_output_channels', 0)} out")
            print(f"    Sample Rate: {device.get('default_samplerate', 'N/A')} Hz")
            print()

    # Show default devices
    print_section("Default Devices")
    try:
        default_input = sd.query_devices(kind='input')
        print(f"Default INPUT device:")
        print(f"  Index: {default_input.get('index', 'N/A')}")
        print(f"  Name: {default_input.get('name', 'N/A')}")
        print(f"  Channels: {default_input.get('max_input_channels', 0)}")
        print()
    except Exception as exc:
        print(f"Error querying default input device: {exc}\n")

    try:
        default_output = sd.query_devices(kind='output')
        print(f"Default OUTPUT device:")
        print(f"  Index: {default_output.get('index', 'N/A')}")
        print(f"  Name: {default_output.get('name', 'N/A')}")
        print(f"  Channels: {default_output.get('max_output_channels', 0)}")
        print()
    except Exception as exc:
        print(f"Error querying default output device: {exc}\n")

    # Recommendations
    print_section("Recommendations for Transcription")

    print("For real-time transcription, you typically need:")
    print("  1. An INPUT device (microphone or loopback)")
    print("  2. If capturing system audio (Zoom/Meet), use a loopback module")
    print()
    print("To set up PulseAudio loopback:")
    print("  pactl load-module module-loopback latency_msec=1")
    print()
    print("To specify a device in .env file, use:")
    print("  AUDIO_DEVICE_INDEX=<index>")
    print()
    print("Where <index> is one of the INPUT device indices listed above.")
    print()

    # Check for common issues
    print_section("Common Issues Check")

    input_devices = [d for d in devices if d.get('max_input_channels', 0) > 0]
    if not input_devices:
        print("⚠️  WARNING: No input devices found!")
        print("   Check your microphone or loopback module configuration.")
    else:
        print(f"✓ Found {len(input_devices)} input device(s)")

    if len(input_devices) > 1:
        print(f"\n⚠️  INFO: Multiple input devices detected ({len(input_devices)})")
        print("   Consider specifying AUDIO_DEVICE_INDEX in .env to avoid")
        print("   unexpected behavior when the default device changes.")

    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
