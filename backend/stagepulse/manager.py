"""Create and control independent stage workers from JSON configuration."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from .audio import FileAudioSource
from .captions import CaptionBus
from .diagnostics import StageDiagnostics
from .models import StageConfig, StageStatus
from .providers import GeminiLiveTranslateProvider, GeminiTranscribeProvider
from .stage import DEFAULT_TRANSLATION_STALL_SECONDS, StageWorker
from .talk_prep import TalkPrepService, TalkTerm, build_terminology, validate_terms
from .terminology import TerminologyNormalizer


class StageManager:
    def __init__(
        self,
        configs: list[StageConfig],
        api_key: str,
        debug_reconnect_after: float | None = None,
        diagnostics: bool = False,
        recover_translation_stall: bool = False,
        translation_stall_seconds: float = DEFAULT_TRANSLATION_STALL_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        if not configs:
            raise ValueError("At least one stage must be configured")
        if debug_reconnect_after is not None and debug_reconnect_after <= 0:
            raise ValueError("Debug reconnect delay must be positive")
        if translation_stall_seconds <= 0:
            raise ValueError("Translation stall threshold must be positive")
        ids = [config.stage_id for config in configs]
        if len(set(ids)) != len(ids):
            raise ValueError("Stage IDs must be unique")
        self.bus = CaptionBus()
        self.talk_prep = TalkPrepService(api_key)
        self._configured_terminology = {
            config.stage_id: deepcopy(config.terminology) for config in configs
        }
        self._applied_talk_terms: dict[str, list[TalkTerm]] = {
            config.stage_id: [] for config in configs
        }
        self.workers: dict[str, StageWorker] = {}
        for config in configs:
            stage_diagnostics = StageDiagnostics(config.stage_id) if diagnostics else None
            if config.target_language:
                if (config.source_language, config.target_language) != ("en", "es"):
                    raise ValueError(
                        f"Stage {config.stage_id}: Gate 3 translation supports en to es"
                    )
                provider = GeminiLiveTranslateProvider(
                    api_key,
                    config.source_language,
                    config.target_language,
                    debug_reconnect_after=debug_reconnect_after,
                    diagnostics=stage_diagnostics,
                )
            else:
                provider = GeminiTranscribeProvider(api_key, config.source_language)
            self.workers[config.stage_id] = StageWorker(
                config,
                FileAudioSource(config.audio_file),
                provider,
                self.bus,
                api_key,
                diagnostics=stage_diagnostics,
                recover_translation_stall=recover_translation_stall,
                translation_stall_seconds=translation_stall_seconds,
            )

    def talk_prep_state(self, stage_id: str) -> dict:
        terms = self._applied_talk_terms[stage_id]
        terminology = self.workers[stage_id].config.terminology or {}
        active_count = len({target for rules in terminology.values() for target in rules.values()})
        return {
            "terms": [term.model_dump() for term in terms],
            "active_count": active_count,
            "editable": self.workers[stage_id].status.state not in {"starting", "running"},
        }

    def apply_talk_terms(self, stage_id: str, terms: list[TalkTerm]) -> dict:
        worker = self.workers[stage_id]
        if worker.status.state in {"starting", "running"}:
            raise RuntimeError("Stop the stage before changing terminology")
        cleaned = validate_terms(terms)
        languages = (worker.config.source_language, worker.config.target_language)
        effective = build_terminology(
            self._configured_terminology[stage_id], cleaned,
            tuple(language for language in languages if language),
        )
        normalizer = TerminologyNormalizer(effective)
        # Replace the existing normalizer only while the stage is idle.
        worker.config = replace(worker.config, terminology=effective)
        worker._terminology = normalizer
        self._applied_talk_terms[stage_id] = cleaned
        return self.talk_prep_state(stage_id)

    @classmethod
    def from_file(
        cls,
        path: Path,
        api_key: str,
        debug_reconnect_after: float | None = None,
        diagnostics: bool = False,
        recover_translation_stall: bool = False,
        translation_stall_seconds: float = DEFAULT_TRANSLATION_STALL_SECONDS,
    ) -> StageManager:
        path = path.resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("stages"), list):
            raise ValueError("Configuration must contain a stages array")
        configs = []
        for item in data["stages"]:
            configs.append(
                StageConfig(
                    stage_id=item["id"],
                    name=item["name"],
                    source_language=item["source_language"],
                    target_language=item.get("target_language"),
                    audio_file=(path.parent / item["audio_file"]).resolve(),
                    terminology=item.get("terminology"),
                )
            )
        return cls(
            configs, api_key, debug_reconnect_after, diagnostics,
            recover_translation_stall, translation_stall_seconds,
        )

    def start(self, stage_id: str) -> None:
        self.workers[stage_id].start()

    def start_all(self) -> None:
        for worker in self.workers.values():
            worker.start()

    async def stop(self, stage_id: str) -> None:
        await self.workers[stage_id].stop()

    async def wait_all(self) -> None:
        for worker in self.workers.values():
            await worker.wait()

    def status(self, stage_id: str) -> StageStatus:
        return self.workers[stage_id].status

    def statuses(self) -> dict[str, StageStatus]:
        return {stage_id: worker.status for stage_id, worker in self.workers.items()}
