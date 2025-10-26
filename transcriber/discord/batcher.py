"""Utilities for batching Discord messages."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Dict, List, Optional

from .notifier import DiscordNotifier


LANG_LABELS = {
    "ja": "日本語",
    "ko": "한국어",
    "en": "English",
}


class DiscordBatcher:
    """Aggregate short transcripts before posting to Discord."""

    def __init__(
        self,
        notifier: DiscordNotifier,
        flush_interval: float = 2.0,
        max_chars: int = 350,
    ) -> None:
        self._notifier = notifier
        self._flush_interval = flush_interval
        self._max_chars = max_chars
        self._buffer: List[str] = []
        self._timer_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def add_entry(self, text: str, translations: Dict[str, str]) -> None:
        if not self._notifier.enabled:
            return
        entry = self._format_message(text, translations)
        async with self._lock:
            if len("\n\n".join(self._buffer + [entry])) > self._max_chars:
                await self._flush_locked()
            self._buffer.append(entry)
            self._schedule_flush()

    def _format_message(self, text: str, translations: Dict[str, str]) -> str:
        parts = [f"Esperanto: {text}"]
        for lang_code, translated in translations.items():
            label = LANG_LABELS.get(lang_code, lang_code.upper())
            parts.append(f"{label}: {translated}")
        return "\n".join(parts)

    def _schedule_flush(self) -> None:
        if self._timer_task and not self._timer_task.done():
            return
        loop = asyncio.get_running_loop()
        self._timer_task = loop.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self._flush_interval)
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        message = "\n\n".join(self._buffer)
        self._buffer.clear()
        await self._notifier.send(message)

    async def close(self) -> None:
        if self._timer_task:
            self._timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._timer_task
            self._timer_task = None
        async with self._lock:
            await self._flush_locked()
