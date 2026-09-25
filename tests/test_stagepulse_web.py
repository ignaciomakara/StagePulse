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
    def test_production_routes_and_audience_qr(self) -> None:
        configs = [
            StageConfig(stage_id, stage_id.title(), "en", "es", Path("unused.wav"))
            for stage_id in ("main", "community", "third")
        ]
        manager = StageManager(configs, "unit-test-placeholder")
        with TestClient(create_app(manager, public_base_url="https://captions.example.test")) as client:
            self.assertEqual(client.get("/control").status_code, 200)
            self.assertIn("/api/stages", client.get("/static/control.js").text)
            self.assertEqual(len(client.get("/api/stages").json()), 3)
            self.assertEqual(client.get("/overlay/main?lang=original").status_code, 200)
            self.assertEqual(client.get("/overlay/main?lang=es").status_code, 200)
            self.assertEqual(client.get("/overlay/main?lang=fr").status_code, 422)
            self.assertEqual(client.get("/overlay/unknown?lang=es").status_code, 404)
            self.assertIn('get("lang") === "es"', client.get("/static/overlay.js").text)
            link = client.get("/api/stages/main/audience-link").json()
            self.assertEqual(link, {
                "url": "https://captions.example.test/audience/main",
                "local_only": False,
            })
            qr = client.get("/api/stages/main/audience-qr.svg")
            self.assertEqual(qr.status_code, 200)
            self.assertIn("image/svg+xml", qr.headers["content-type"])
            self.assertIn("<svg", qr.text)
            self.assertEqual(client.get("/api/stages/unknown/audience-qr.svg").status_code, 404)
        with TestClient(create_app(manager)) as client:
            local = client.get("/api/stages/main/audience-link").json()
            self.assertEqual(local["url"], "http://testserver/audience/main")
            self.assertFalse(local["local_only"])
            localhost = client.get(
                "/api/stages/main/audience-link", headers={"host": "127.0.0.1:8000"}
            ).json()
            self.assertTrue(localhost["local_only"])
        with self.assertRaises(ValueError):
            create_app(manager, public_base_url="file:///not-a-public-origin")

    def test_overlay_and_audience_share_caption_bus_without_provider_start(self) -> None:
        from datetime import datetime, timezone

        from stagepulse.models import CaptionEvent

        manager = StageManager(
            [StageConfig("main", "Main", "en", "es", Path("unused.wav"))],
            "unit-test-placeholder",
        )
        worker = manager.workers["main"]
        with TestClient(create_app(manager)) as client:
            with client.websocket_connect("/ws/stages/main/captions") as audience:
                with client.websocket_connect("/ws/stages/main/captions") as overlay_en:
                    with client.websocket_connect("/ws/stages/main/captions") as overlay_es:
                        self.assertEqual(client.get("/api/stages/main").json()["viewers"], 3)
                        for language, text in (("en", "DOS works."), ("es", "DOS funciona.")):
                            manager.bus.publish(CaptionEvent(
                                stage_id="main", language=language, text=text,
                                is_final=True, timestamp=datetime.now(timezone.utc),
                                provider="unit-test-provider",
                            ))
                            for subscriber in (audience, overlay_en, overlay_es):
                                event = subscriber.receive_json()
                                self.assertEqual((event["language"], event["text"]), (language, text))
                        self.assertEqual(worker.status.connections, 0)
                        self.assertEqual(worker.status.state, "created")

    def test_late_audience_websocket_receives_current_caption(self) -> None:
        from datetime import datetime, timezone
        from stagepulse.models import CaptionEvent

        manager = StageManager(
            [StageConfig("main", "Main", "en", "es", Path("unused.wav"))],
            "unit-test-placeholder",
        )
        manager.bus.publish(CaptionEvent(
            "main", "es", "DOS funciona.", True,
            datetime.now(timezone.utc), "unit-test-provider",
        ))
        with TestClient(create_app(manager)) as client:
            with client.websocket_connect("/ws/stages/main/captions") as late:
                self.assertEqual(late.receive_json()["text"], "DOS funciona.")
                self.assertEqual(manager.status("main").connections, 0)

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
