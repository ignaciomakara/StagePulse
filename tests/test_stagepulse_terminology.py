"""Tests for explicit caption terminology normalization."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.captions import CaptionBus
from stagepulse.manager import StageManager
from stagepulse.models import StageConfig
from stagepulse.providers import ProviderTranscript
from stagepulse.stage import StageWorker
from stagepulse.terminology import TerminologyNormalizer


class TerminologyNormalizerTests(unittest.TestCase):
    def test_configuration_terminology_is_optional_per_stage(self) -> None:
        stages = [
            {
                "id": "with-terms", "name": "With terms", "source_language": "en",
                "target_language": "es", "audio_file": "unused.wav",
                "terminology": {"en": {"Word Perfect": "WordPerfect"}},
            },
            {
                "id": "without-terms", "name": "Without terms", "source_language": "en",
                "target_language": "es", "audio_file": "unused.wav",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_file = Path(temporary_dir) / "stages.json"
            config_file.write_text(json.dumps({"stages": stages}), encoding="utf-8")
            manager = StageManager.from_file(config_file, "unit-test-placeholder")
        self.assertEqual(
            manager.workers["with-terms"].config.terminology["en"],
            {"Word Perfect": "WordPerfect"},
        )
        self.assertIsNone(manager.workers["without-terms"].config.terminology)

    def test_explicit_replacement_respects_language_and_word_edges(self) -> None:
        normalizer = TerminologyNormalizer({
            "en": {"Word Perfect": "WordPerfect"},
            "es": {"Word Perfect": "WordPerfect ES"},
        })
        self.assertEqual(
            normalizer.apply("en", "word perfect and Word Perfect; SuperWord Perfectly"),
            "WordPerfect and WordPerfect; SuperWord Perfectly",
        )
        self.assertEqual(normalizer.apply("es", "Word Perfect"), "WordPerfect ES")
        self.assertEqual(normalizer.apply("fr", "Word Perfect"), "Word Perfect")
        self.assertEqual(TerminologyNormalizer().apply("en", "Word Perfect"), "Word Perfect")
        no_cascade = TerminologyNormalizer({"en": {"Word Perfect": "WordPerfect", "WordPerfect": "WP"}})
        self.assertEqual(no_cascade.apply("en", "Word Perfect and WordPerfect"), "WordPerfect and WP")

    def test_invalid_configuration_is_visible(self) -> None:
        for configuration in ([], {"en": []}, {"en": {"": "value"}}, {"en": {"term": ""}}):
            with self.subTest(configuration=configuration), self.assertRaises(ValueError):
                TerminologyNormalizer(configuration)

    def test_previews_and_finals_are_normalized_before_publish(self) -> None:
        class IdleProvider:
            name = "unit-test-provider"

        bus = CaptionBus()
        subscription = bus.subscribe("main")
        worker = StageWorker(
            StageConfig(
                "main", "Main", "en", "es", Path("unused.wav"),
                {"en": {"Word Perfect": "WordPerfect"}},
            ),
            None,
            IdleProvider(),
            bus,
            "unit-test-placeholder",
        )
        worker._on_transcript(ProviderTranscript("en", "I used Word Perfect every day", "interim"))
        preview = subscription.queue.get_nowait()
        self.assertFalse(preview.is_final)
        self.assertEqual(preview.text, "I used WordPerfect every day")
        worker._on_transcript(ProviderTranscript("en", "I used Word Perfect every day.", "final"))
        final = subscription.queue.get_nowait()
        self.assertTrue(final.is_final)
        self.assertEqual(final.text, "I used WordPerfect every day.")
        worker._on_transcript(ProviderTranscript("es", "Word Perfect era popular.", "final"))
        spanish = subscription.queue.get_nowait()
        self.assertEqual(spanish.text, "Word Perfect era popular.")
        subscription.close()


if __name__ == "__main__":
    unittest.main()
