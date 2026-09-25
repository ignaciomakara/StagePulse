"""Tests for caption assembly and stage coordination without network access."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.captions import CaptionAssembler, CaptionBus
from stagepulse.manager import StageManager
from stagepulse.models import CaptionEvent, StageConfig
from stagepulse.stage import StageWorker


class CaptionAssemblerTests(unittest.TestCase):
    def test_short_fragments_form_a_preview_and_final_unit(self) -> None:
        assembler = CaptionAssembler()
        self.assertEqual(assembler.fragment("The first", 0.0), [])
        preview = assembler.fragment("personal computer", 0.2)
        self.assertEqual(len(preview), 1)
        self.assertFalse(preview[0].is_final)
        self.assertEqual(preview[0].text, "The first personal computer")
        self.assertEqual(assembler.fragment("personal computer", 0.3), [])
        final = assembler.fragment("ran DOS.", 0.4)
        self.assertEqual(len(final), 1)
        self.assertTrue(final[0].is_final)
        self.assertEqual(final[0].text, "The first personal computer ran DOS.")
        self.assertEqual(assembler.flush(), [])
        assembler.fragment("A separate caption", 0.5)
        self.assertEqual(assembler.flush()[0].text, "A separate caption")
        self.assertEqual(final[0].text, "The first personal computer ran DOS.")

    def test_source_and_target_buffers_are_independent(self) -> None:
        english = CaptionAssembler()
        spanish = CaptionAssembler()
        english.fragment("WordPerfect", 0.0)
        spanish.fragment("un procesador de texto", 0.0)
        self.assertEqual(english.flush()[0].text, "WordPerfect")
        self.assertEqual(spanish.flush()[0].text, "un procesador de texto")

    def test_final_snapshot_replaces_interim_text(self) -> None:
        assembler = CaptionAssembler()
        assembler.interim_snapshot("The DOS machine", 0.0)
        final = assembler.final_snapshot("The DOS computer.")
        self.assertEqual(final[0].text, "The DOS computer.")
        self.assertTrue(final[0].is_final)
        self.assertEqual(assembler.flush(), [])


class StageCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bus_fans_out_only_to_matching_stage(self) -> None:
        bus = CaptionBus()
        first = bus.subscribe("main")
        second = bus.subscribe("main")
        other = bus.subscribe("community")
        event = CaptionEvent(
            "main", "en", "The first computer", False,
            datetime.now(timezone.utc), "provider",
        )
        bus.publish(event)
        self.assertIs(first.queue.get_nowait(), event)
        self.assertIs(second.queue.get_nowait(), event)
        self.assertTrue(other.queue.empty())
        self.assertEqual(bus.subscriber_count("main"), 2)
        first.close()
        second.close()
        other.close()

    async def test_late_subscriber_gets_only_latest_caption_per_language(self) -> None:
        bus = CaptionBus()
        def event(language: str, text: str) -> CaptionEvent:
            return CaptionEvent(
                "main", language, text, True, datetime.now(timezone.utc), "provider"
            )

        old_en = event("en", "Old English")
        latest_en = event("en", "Current English")
        latest_es = event("es", "Español actual")
        bus.publish(old_en)
        bus.publish(latest_en)
        bus.publish(latest_es)
        late = bus.subscribe("main")
        self.assertEqual(
            {late.queue.get_nowait(), late.queue.get_nowait()},
            {latest_en, latest_es},
        )
        self.assertTrue(late.queue.empty())
        live = event("es", "Español en vivo")
        bus.publish(live)
        self.assertIs(late.queue.get_nowait(), live)
        late.close()

        bus.clear_latest("main")
        restarted = bus.subscribe("main")
        self.assertTrue(restarted.queue.empty())
        restarted.close()

    async def test_three_stages_are_created_from_configuration(self) -> None:
        configs = [
            StageConfig(
                stage_id=f"stage-{index}",
                name=f"Stage {index}",
                source_language="en",
                target_language="es",
                audio_file=Path("unused.wav"),
            )
            for index in range(3)
        ]
        manager = StageManager(configs, "unit-test-placeholder")
        self.assertEqual(len(manager.workers), 3)
        self.assertEqual(set(manager.statuses()), {"stage-0", "stage-1", "stage-2"})
        self.assertTrue(all(status.connections == 0 for status in manager.statuses().values()))

    async def test_one_worker_failure_does_not_stop_another(self) -> None:
        class FailingProvider:
            name = "test-failing-provider"
            _resume_handle = "test-sensitive-handle"

            async def run(self, audio, on_transcript, on_connected) -> None:
                on_connected()
                raise RuntimeError("provider failed: test-sensitive-handle")

        class CompletingProvider:
            name = "test-completing-provider"

            async def run(self, audio, on_transcript, on_connected) -> None:
                on_connected()
                await asyncio.sleep(0.01)

        bus = CaptionBus()
        first = StageConfig("first", "First", "en", "es", Path("unused.wav"))
        second = StageConfig("second", "Second", "en", "es", Path("unused.wav"))
        failing = StageWorker(first, None, FailingProvider(), bus, "test-placeholder")
        completing = StageWorker(second, None, CompletingProvider(), bus, "test-placeholder")
        failing.start()
        completing.start()
        await asyncio.gather(failing.wait(), completing.wait())
        self.assertEqual(failing.status.state, "failed")
        self.assertEqual(failing.status.error, "provider failed: [REDACTED_HANDLE]")
        self.assertEqual(completing.status.state, "completed")

    async def test_one_worker_reconnect_does_not_restart_another(self) -> None:
        class RecoveringProvider:
            name = "test-recovering-provider"

            async def run(self, audio, on_transcript, on_connected) -> None:
                on_connected()
                await asyncio.sleep(0.01)
                on_connected()

        class OtherProvider:
            name = "test-other-provider"

            async def run(self, audio, on_transcript, on_connected) -> None:
                on_connected()
                await asyncio.sleep(0.03)

        bus = CaptionBus()
        first = StageConfig("first", "First", "en", "es", Path("unused.wav"))
        second = StageConfig("second", "Second", "en", "es", Path("unused.wav"))
        recovering = StageWorker(first, None, RecoveringProvider(), bus, "placeholder")
        other = StageWorker(second, None, OtherProvider(), bus, "placeholder")
        recovering.start()
        other.start()
        await asyncio.gather(recovering.wait(), other.wait())
        self.assertEqual(recovering.status.connections, 2)
        self.assertEqual(other.status.connections, 1)
        self.assertEqual(other.status.state, "completed")


if __name__ == "__main__":
    unittest.main()
