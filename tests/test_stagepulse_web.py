"""Local tests for browser audio and the web bridge."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.audio import BrowserAudioSource
from stagepulse.manager import StageManager
from stagepulse.models import StageConfig
from stagepulse.providers import ProviderTranscript
from stagepulse.web import create_app


class BrowserAudioSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_frames_and_close(self) -> None:
        source = BrowserAudioSource()
        source.feed(bytes(3200))
        source.close()
        self.assertEqual([chunk async for chunk in source.chunks()], [bytes(3200)])
        with self.assertRaises(RuntimeError):
            source.feed(bytes(3200))

    async def test_rejects_invalid_frames(self) -> None:
        source = BrowserAudioSource()
        with self.assertRaises(ValueError):
            source.feed(b"\x00")

    async def test_browser_ingress_is_bounded_by_pcm_bytes(self) -> None:
        source = BrowserAudioSource()
        for _ in range(32):
            source.feed(bytes(3200))
        with self.assertRaises(RuntimeError):
            source.feed(bytes(3200))
        source.close()
        self.assertEqual(len([chunk async for chunk in source.chunks()]), 32)


class WebBridgeTests(unittest.TestCase):
    def test_reconnect_and_viewers_reuse_one_worker(self) -> None:
        class CountingProvider:
            name = "local-test-provider"

            def __init__(self) -> None:
                self.calls = 0

            async def run(self, audio, on_transcript, on_connected) -> None:
                self.calls += 1
                on_connected()
                async for _ in audio.chunks():
                    on_transcript(ProviderTranscript("en", "DOS works well.", "fragment"))
                    on_transcript(ProviderTranscript("es", "DOS funciona bien.", "fragment"))

        config = StageConfig("main", "Main", "en", "es", Path("unused.wav"))
        manager = StageManager([config], "unit-test-placeholder")
        provider = CountingProvider()
        manager.workers["main"].provider = provider

        with TestClient(create_app(manager)) as client:
            self.assertEqual(client.get("/stage").status_code, 200)
            self.assertEqual(client.get("/audience/main").status_code, 200)
            with client.websocket_connect("/ws/stages/main/captions") as first:
                with client.websocket_connect("/ws/stages/main/captions") as second:
                    with client.websocket_connect("/ws/stages/main/audio") as audio:
                        self.assertFalse(audio.receive_json()["resumed"])
                        audio.send_bytes(bytes(3200))
                        self.assertEqual(first.receive_json()["text"], "DOS works well.")
                        self.assertEqual(second.receive_json()["text"], "DOS works well.")
                        self.assertEqual(first.receive_json()["text"], "DOS funciona bien.")
                        self.assertEqual(second.receive_json()["text"], "DOS funciona bien.")
                        self.assertEqual(client.get("/api/stages/main").json()["viewers"], 2)
                        health = client.get("/api/stages/main").json()
                        self.assertTrue(health["browser_connected"])
                        self.assertTrue(health["provider_connected"])
                        self.assertEqual(health["connection_count"], 1)
                        self.assertIsNotNone(health["last_caption_at"])
                        self.assertEqual(provider.calls, 1)
                        with self.assertRaises(WebSocketDisconnect):
                            with client.websocket_connect("/ws/stages/main/audio") as duplicate:
                                duplicate.receive_json()
                    with client.websocket_connect("/ws/stages/main/audio") as reconnected:
                        self.assertTrue(reconnected.receive_json()["resumed"])
                        self.assertEqual(provider.calls, 1)
                        self.assertEqual(client.post("/api/stages/main/stop").json()["state"], "stopped")
            self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
