#!/usr/bin/env python3
"""Cross-platform audio diagnostic helper for the transcription pipeline."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from transcriber.audio_setup import AudioEnvironmentError, run_cli_diagnostics
    from transcriber.config import load_settings

    try:
        settings = load_settings()
        run_cli_diagnostics(settings.audio)
    except AudioEnvironmentError as exc:
        print(f"Audio environment error: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error during diagnostics: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
