"""Tests for optional pre-talk suggestions and approved terminology."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.manager import StageManager
from stagepulse.models import StageConfig
from stagepulse.providers import ProviderTranscript
from stagepulse.talk_prep import (
    SUGGESTION_MODEL,
    TalkMetadata,
    TalkPrepError,
    TalkPrepService,
    TalkTerm,
    validate_terms,
)
from stagepulse.web import create_app


def manager() -> StageManager:
    return StageManager(
        [
            StageConfig("main", "Main", "en", "es", Path("unused.wav"),
                        {"en": {"Alpha DB": "AlphaDB"}}),
            StageConfig("community", "Community", "en", "es", Path("unused.wav")),
        ],
        "unit-test-placeholder",
    )


class TalkPrepTests(unittest.TestCase):
    def test_structured_suggestion_response_and_model(self) -> None:
        client = SimpleNamespace(models=SimpleNamespace())
        calls = []

        def generate_content(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='{"terms":[{"canonical":"NebulaDB","variants":["Nebula DB","nebula db"]}]}')

        client.models.generate_content = generate_content
        context = MagicMock()
        context.__enter__.return_value = client
        with patch("stagepulse.talk_prep.genai.Client", return_value=context):
            terms = TalkPrepService("unit-test-placeholder").suggest(
                TalkMetadata(title="NebulaDB internals", speaker="Alex", abstract="NebulaDB storage engine")
            )
        self.assertEqual([term.model_dump() for term in terms], [
            {"canonical": "NebulaDB", "variants": ["Nebula DB"]},
        ])
        self.assertEqual(calls[0]["model"], SUGGESTION_MODEL)
        self.assertEqual(calls[0]["config"].response_mime_type, "application/json")
        self.assertIsNotNone(calls[0]["config"].response_schema)
        self.assertTrue(calls[0]["config"].automatic_function_calling.disable)

    def test_empty_metadata_and_invalid_terms_are_rejected(self) -> None:
        for title, abstract in (("", "details"), ("Talk", " ")):
            with self.subTest(title=title, abstract=abstract), self.assertRaises(ValidationError):
                TalkMetadata(title=title, speaker="", abstract=abstract)
        with self.assertRaises(ValueError):
            validate_terms([TalkTerm(canonical=" ")])
        with self.assertRaises(ValueError):
            validate_terms([TalkTerm(canonical="One", variants=["Shared"]),
                            TalkTerm(canonical="Two", variants=["shared"])])

    def test_operator_application_is_stage_specific_and_preserves_config(self) -> None:
        stages = manager()
        self.assertEqual(stages.talk_prep_state("main")["active_count"], 1)
        self.assertEqual(stages.talk_prep_state("community")["active_count"], 0)
        self.assertEqual(stages.workers["main"]._terminology.apply("en", "Nebula DB"), "Nebula DB")
        applied = stages.apply_talk_terms("main", [TalkTerm(canonical="NebulaDB", variants=["Nebula DB"])])
        self.assertEqual(applied["active_count"], 2)
        self.assertEqual(stages.workers["main"]._terminology.apply("en", "Alpha DB and Nebula DB"),
                         "AlphaDB and NebulaDB")
        self.assertEqual(stages.workers["main"]._terminology.apply("es", "Nebula DB"), "NebulaDB")
        self.assertEqual(stages.workers["community"]._terminology.apply("en", "Nebula DB"), "Nebula DB")
        with patch.object(stages.talk_prep, "suggest") as suggest:
            stages.workers["main"]._on_transcript(
                ProviderTranscript("en", "Nebula DB is running.", "final")
            )
            suggest.assert_not_called()
        subscription = stages.bus.subscribe("main")
        self.assertEqual(subscription.queue.get_nowait().text, "NebulaDB is running.")
        subscription.close()
        stages.workers["main"]._state = "running"
        with self.assertRaises(RuntimeError):
            stages.apply_talk_terms("main", [TalkTerm(canonical="Another")])

    def test_manual_terms_work_after_ai_failure_and_are_not_auto_applied(self) -> None:
        stages = manager()
        metadata = {"title": "NebulaDB internals", "speaker": "Alex", "abstract": "NebulaDB storage engine"}
        with TestClient(create_app(stages)) as client:
            with patch.object(stages.talk_prep, "suggest", return_value=[TalkTerm(canonical="NebulaDB")]):
                suggested = client.post("/api/stages/main/talk-prep/suggestions", json=metadata)
            self.assertEqual(suggested.status_code, 200)
            self.assertEqual(stages.talk_prep_state("main")["terms"], [])
            with patch.object(stages.talk_prep, "suggest", side_effect=TalkPrepError("Gemini unavailable")):
                failed = client.post("/api/stages/main/talk-prep/suggestions", json=metadata)
            self.assertEqual(failed.status_code, 502)
            manual = client.post("/api/stages/main/talk-prep", json={
                "terms": [{"canonical": "OrionDB", "variants": ["Orion DB"]}],
            })
            self.assertEqual(manual.status_code, 200)
            self.assertEqual(manual.json()["terms"][0]["canonical"], "OrionDB")
            self.assertEqual(stages.workers["main"]._terminology.apply("en", "Orion DB"), "OrionDB")
            self.assertEqual(client.get("/api/stages/community/talk-prep").json()["terms"], [])
            self.assertEqual(client.post("/api/stages/main/talk-prep/suggestions", json={
                "title": "", "speaker": "", "abstract": "",
            }).status_code, 422)

    def test_no_sample_specific_vocabulary_is_built_in(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "backend/stagepulse/talk_prep.py").read_text(encoding="utf-8")
        for name in ("FreeDOS", "WordPerfect", "MS-DOS", "Linux"):
            self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()
