"""Synthetic detector tests; these do not represent Gemini benchmark evidence."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.audio import BrowserAudioSource
from stagepulse.captions import CaptionBus
from stagepulse.models import StageConfig
from stagepulse.manager import StageManager
from stagepulse.providers import (
    GeminiLiveTranslateProvider, ProviderTranscript, _BufferedAudio,
)
from stagepulse.stage import StageWorker
from stagepulse.web import create_app


class ProbeProvider:
    name = "test-provider"
    provider_status = "connected"
    reconnect_count = 0

    def __init__(self) -> None:
        self.requests = 0

    def request_translation_stall_reconnect(self) -> bool:
        self.requests += 1
        return True


class TranslationStallTests(unittest.TestCase):
    def worker(self, recover: bool = False) -> tuple[StageWorker, ProbeProvider]:
        provider = ProbeProvider()
        worker = StageWorker(
            StageConfig("main", "Main", "en", "es", Path("unused.wav")),
            BrowserAudioSource(), provider, CaptionBus(), "placeholder",
            recover_translation_stall=recover,
        )
        worker._state = "running"
        worker._audio_received(bytes(3200), 1)
        worker._on_transcript(ProviderTranscript("es", "Traducción inicial.", "fragment"))
        worker._last_raw_es_at = time.monotonic() - 7
        return worker, provider

    def test_two_recent_english_events_confirm_one_stall_and_one_debug_request(self) -> None:
        worker, provider = self.worker(recover=True)
        worker._on_transcript(ProviderTranscript("en", "First continued phrase", "fragment"))
        self.assertFalse(worker.status.translation_stall_active)
        worker._on_transcript(ProviderTranscript("en", "Second continued phrase", "fragment"))
        self.assertTrue(worker.status.translation_stall_active)
        self.assertEqual(worker.status.translation_status, "delayed")
        self.assertEqual(worker.status.translation_stall_count, 1)
        self.assertIsNotNone(worker.status.translation_stall_started_at)
        self.assertEqual(provider.requests, 1)
        worker._on_transcript(ProviderTranscript("en", "Third continued phrase", "fragment"))
        self.assertEqual(provider.requests, 1)
        provider.provider_status = "reconnecting"
        self.assertFalse(worker.status.translation_stall_active)
        provider.provider_status = "connected"
        worker._on_transcript(ProviderTranscript("es", "La traducción volvió.", "fragment"))
        self.assertFalse(worker.status.translation_stall_active)
        self.assertEqual(worker.status.translation_status, "ok")

    def test_silence_disconnection_and_audio_end_are_not_stalls(self) -> None:
        for condition in ("silence", "disconnected", "audio_end"):
            with self.subTest(condition=condition):
                worker, provider = self.worker()
                if condition == "disconnected":
                    provider.provider_status = "reconnecting"
                if condition == "audio_end":
                    worker._audio_finished()
                if condition == "silence":
                    worker._last_pcm_at = time.monotonic() - 3
                worker._on_transcript(ProviderTranscript("en", "A continued phrase", "fragment"))
                worker._on_transcript(ProviderTranscript("en", "Another continued phrase", "fragment"))
                self.assertFalse(worker.status.translation_stall_active)
                self.assertEqual(worker.status.translation_stall_count, 0)
                self.assertEqual(provider.requests, 0)

    def test_recovery_is_off_by_default(self) -> None:
        worker, provider = self.worker()
        worker._on_transcript(ProviderTranscript("en", "One continued phrase", "fragment"))
        worker._on_transcript(ProviderTranscript("en", "Two continued phrase", "fragment"))
        self.assertTrue(worker.status.translation_stall_active)
        self.assertEqual(provider.requests, 0)

    def test_missing_initial_spanish_can_be_detected_after_continued_english(self) -> None:
        provider = ProbeProvider()
        worker = StageWorker(
            StageConfig("main", "Main", "en", "es", Path("unused.wav")),
            BrowserAudioSource(), provider, CaptionBus(), "placeholder",
        )
        worker._state = "running"
        worker._audio_received(bytes(3200), 1)
        worker._on_transcript(ProviderTranscript("en", "First English phrase", "fragment"))
        worker._first_raw_en_at = time.monotonic() - 7
        worker._recent_en.clear()  # The first phrase is now outside the recent window.
        worker._on_transcript(ProviderTranscript("en", "Second English phrase", "fragment"))
        worker._on_transcript(ProviderTranscript("en", "Third English phrase", "fragment"))
        self.assertTrue(worker.status.translation_stall_active)
        self.assertIsNone(worker.status.age_last_raw_es)
        self.assertEqual(provider.requests, 0)

    def test_existing_health_api_exposes_confirmed_stall(self) -> None:
        config = StageConfig("main", "Main", "en", "es", Path("unused.wav"))
        manager = StageManager([config], "placeholder")
        worker = manager.workers["main"]
        worker.provider = ProbeProvider()
        worker._state = "running"
        worker._audio_received(bytes(3200), 1)
        worker._on_transcript(ProviderTranscript("es", "Traducción inicial.", "fragment"))
        worker._last_raw_es_at = time.monotonic() - 7
        worker._on_transcript(ProviderTranscript("en", "First continued phrase", "fragment"))
        worker._on_transcript(ProviderTranscript("en", "Second continued phrase", "fragment"))
        with TestClient(create_app(manager)) as client:
            health = client.get("/api/stages/main").json()
        self.assertTrue(health["translation_stall_active"])
        self.assertEqual(health["translation_status"], "delayed")
        self.assertEqual(health["translation_stall_count"], 1)
        self.assertIsNotNone(health["translation_stall_started_at"])
        self.assertGreaterEqual(health["age_last_raw_es"], 6)


class TranslationStallBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_english_and_spanish_in_same_provider_response_do_not_reconnect(self) -> None:
        provider = ProbeProvider()
        worker = StageWorker(
            StageConfig("main", "Main", "en", "es", Path("unused.wav")),
            BrowserAudioSource(), provider, CaptionBus(), "placeholder",
            recover_translation_stall=True,
        )
        worker._state = "running"
        worker._audio_received(bytes(3200), 1)
        worker._on_transcript(ProviderTranscript("es", "Earlier translation", "fragment"))
        worker._last_raw_es_at = time.monotonic() - 7
        worker._on_transcript(ProviderTranscript("en", "One English fragment", "fragment"))
        worker._on_transcript(ProviderTranscript("en", "Second English fragment", "fragment"))
        worker._on_transcript(ProviderTranscript("es", "Current translation", "fragment"))
        await asyncio.sleep(0)
        self.assertEqual(worker.status.translation_stall_count, 0)
        self.assertEqual(provider.requests, 0)

    async def test_recovery_request_closes_current_connection(self) -> None:
        """Synthetic session lifecycle check, not a Gemini effectiveness test."""
        provider = GeminiLiveTranslateProvider("placeholder", "en", "es")
        counts = {"opened": 0, "closed": 0, "active": 0}

        class Session:
            async def send_realtime_input(self, **_kwargs) -> None:
                pass

            async def receive(self):
                while True:
                    await asyncio.sleep(3600)
                    yield None

        class Connection:
            async def __aenter__(self):
                counts["opened"] += 1
                counts["active"] += 1
                return Session()

            async def __aexit__(self, *_args):
                counts["active"] -= 1
                counts["closed"] += 1

        class Live:
            def connect(self, **_kwargs):
                return Connection()

        client = SimpleNamespace(aio=SimpleNamespace(live=Live()))

        def connected() -> None:
            self.assertTrue(provider.request_translation_stall_reconnect())
            self.assertFalse(provider.request_translation_stall_reconnect())

        reason = await asyncio.wait_for(
            provider._run_connection(client, _BufferedAudio(), lambda _part: None, connected),
            timeout=2,
        )
        self.assertEqual(reason, "translation_stall")
        self.assertEqual(counts, {"opened": 1, "closed": 1, "active": 0})
        self.assertFalse(provider._reconnect_requested.is_set())


if __name__ == "__main__":
    unittest.main()
