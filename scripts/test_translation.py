#!/usr/bin/env python3
"""Quick smoke test for the translation service configuration."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Iterable

from dotenv import load_dotenv

if __name__ == "__main__":
    # allow running from scripts/ by adding project root
    from pathlib import Path
    import sys

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

from transcriber.config import load_settings
from transcriber.translate import TranslationService


async def translate_text(text: str) -> None:
    settings = load_settings()
    cfg = settings.translation
    service = TranslationService(
        enabled=cfg.enabled,
        source_language=cfg.source_language,
        targets=cfg.targets,
        provider=cfg.provider,
        libre_url=cfg.libre_url,
        libre_api_key=cfg.libre_api_key,
        timeout=cfg.timeout_seconds,
        google_api_key=cfg.google_api_key,
        google_model=cfg.google_model,
        google_credentials_path=cfg.google_credentials_path,
    )
    try:
        result = await service.translate(text)
    finally:
        await service.close()

    if not cfg.enabled or not cfg.targets:
        print("Translation disabled or no targets configured. Nothing to do.")
        return

    if not result.translations:
        print("No translations were returned.")
        return

    print(f"Source ({cfg.source_language}): {result.text}")
    for lang, translated in result.translations.items():
        print(f"→ {lang}: {translated}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate translation settings by translating a sample sentence."
    )
    parser.add_argument(
        "text",
        nargs="?",
        default="Ĉu vi komprenas min?",
        help="Text to translate (default: 'Ĉu vi komprenas min?').",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str]) -> int:
    load_dotenv()
    args = parse_args(argv)
    asyncio.run(translate_text(args.text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
