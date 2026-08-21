"""Tests for translation caching semantics."""

from __future__ import annotations

import unittest

from transcriber.translate.service import TranslationService


class TranslationCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_failure_is_not_cached_and_retries_succeed(self) -> None:
        service = TranslationService(enabled=True, targets=["ja", "ko"], provider="libre")
        calls: list[str] = []
        fail_ko = True

        async def fake_single(text: str, target: str):
            calls.append(target)
            if target == "ko" and fail_ko:
                raise RuntimeError("HTTP 503")
            return f"{target}訳"

        service._translate_single = fake_single  # type: ignore[method-assign]

        first = await service.translate("Saluton.")
        self.assertEqual(first.translations, {"ja": "ja訳"})

        fail_ko = False
        second = await service.translate("Saluton.")

        self.assertEqual(second.translations, {"ja": "ja訳", "ko": "ko訳"})
        self.assertEqual(calls.count("ko"), 2)
        await service.close()

    async def test_complete_results_are_served_from_cache(self) -> None:
        service = TranslationService(enabled=True, targets=["ja"], provider="libre")
        calls: list[str] = []

        async def fake_single(text: str, target: str):
            calls.append(target)
            return "こんにちは"

        service._translate_single = fake_single  # type: ignore[method-assign]

        await service.translate("Saluton.")
        again = await service.translate("Saluton.")

        self.assertEqual(again.translations, {"ja": "こんにちは"})
        self.assertEqual(len(calls), 1)
        await service.close()


if __name__ == "__main__":
    unittest.main()
