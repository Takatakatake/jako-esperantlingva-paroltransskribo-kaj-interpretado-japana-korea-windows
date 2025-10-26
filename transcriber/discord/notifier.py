"""Discord notifications via webhook."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp


class DiscordNotifier:
    """Post final transcripts to a Discord channel using a webhook."""

    def __init__(self, webhook_url: Optional[str], username: str = "Esperanto STT", enabled: bool = False) -> None:
        self.webhook_url = webhook_url
        self.username = username
        self.enabled = enabled and bool(webhook_url)
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> Optional[aiohttp.ClientSession]:
        if not self.enabled:
            return None
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return
        async with self._lock:
            session = await self._ensure_session()
            if not session:
                return
            try:
                payload = {"content": text.strip(), "username": self.username}
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        logging.error("Discord webhook failed (%s): %s", resp.status, body)
            except Exception as exc:  # noqa: BLE001
                logging.exception("Failed to post to Discord webhook: %s", exc)

