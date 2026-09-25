"""Independent lifecycle for a single configured stage."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .audio import AudioSource
from .captions import CaptionAssembler, CaptionBus
from .diagnostics import StageDiagnostics
from .models import CaptionEvent, StageConfig, StageStatus
from .providers import CaptionProvider, ProviderTranscript
from .terminology import TerminologyNormalizer


class StageWorker:
    def __init__(
        self,
        config: StageConfig,
        audio: AudioSource,
        provider: CaptionProvider,
        bus: CaptionBus,
        api_key: str,
        diagnostics: StageDiagnostics | None = None,
    ) -> None:
        self.config = config
        self.audio = audio
        self.provider = provider
        self.bus = bus
        self._api_key = api_key
        self.diagnostics = diagnostics
        self._state = "created"
        self._error: str | None = None
        self._connections = 0
        self._task: asyncio.Task | None = None
        self._assemblers: dict[str, CaptionAssembler] = {}
        self._terminology = TerminologyNormalizer(config.terminology)
        self._session_started_at: datetime | None = None
        self._connection_started_at: datetime | None = None
        self._last_caption_at: datetime | None = None

    @property
    def status(self) -> StageStatus:
        return StageStatus(
            stage_id=self.config.stage_id,
            name=self.config.name,
            state=self._state,
            provider=self.provider.name,
            connections=self._connections,
            error=self._error,
            provider_status=getattr(
                self.provider,
                "provider_status",
                "connected" if self._state == "running" else self._state,
            ),
            reconnect_count=getattr(self.provider, "reconnect_count", 0),
            last_error=getattr(self.provider, "last_error", None) or self._error,
            last_provider_event=getattr(self.provider, "last_provider_event", None),
            last_provider_event_at=getattr(self.provider, "last_provider_event_at", None),
            latest_resumption_handle_available=getattr(
                self.provider, "latest_resumption_handle_available", False
            ),
            last_audio_at=getattr(self.provider, "last_audio_at", None),
            last_caption_at=self._last_caption_at,
            session_started_at=self._session_started_at,
            connection_started_at=self._connection_started_at,
            go_away_count=getattr(self.provider, "go_away_count", 0),
            go_away_at=getattr(self.provider, "go_away_at", None),
            go_away_time_left=getattr(self.provider, "go_away_time_left", None),
            resumption_update_count=getattr(
                self.provider, "resumption_update_count", 0
            ),
            forced_reconnect_count=getattr(
                self.provider, "forced_reconnect_count", 0
            ),
            dropped_audio_bytes=getattr(self.provider, "dropped_audio_bytes", 0),
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError(f"Stage {self.config.stage_id} is already running")
        self._state = "starting"
        self._error = None
        self._assemblers = {}
        self._session_started_at = datetime.now(timezone.utc)
        self._connection_started_at = None
        self._last_caption_at = None
        self.bus.clear_latest(self.config.stage_id)
        if self.diagnostics is not None:
            self.audio.on_chunk = lambda pcm: self.diagnostics.record(
                "pcm", bytes=len(pcm)
            )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def wait(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _connected(self) -> None:
        self._connections += 1
        self._connection_started_at = datetime.now(timezone.utc)
        self._state = "running"

    def _on_transcript(self, fragment: ProviderTranscript) -> None:
        assembler = self._assemblers.setdefault(fragment.language, CaptionAssembler())
        now = time.monotonic()
        raw_received_at = None
        if self.diagnostics is not None:
            raw_received_at = self.diagnostics.record(
                "provider_raw" if fragment.kind != "boundary" else "provider_boundary",
                language=fragment.language,
                kind=fragment.kind,
                text=fragment.text,
            )
        if fragment.kind == "fragment":
            units = assembler.fragment(fragment.text, now)
        elif fragment.kind == "interim":
            units = assembler.interim_snapshot(fragment.text, now)
        elif fragment.kind == "final":
            units = assembler.final_snapshot(fragment.text)
        elif fragment.kind == "boundary":
            units = assembler.flush()
        else:
            raise ValueError(f"Unknown provider transcript kind: {fragment.kind}")
        if self.diagnostics is not None:
            self.diagnostics.record(
                "assembler", language=fragment.language, kind=fragment.kind,
                decision=assembler.last_decision, produced=len(units),
            )
        for unit in units:
            self._publish(fragment.language, unit.text, unit.is_final, raw_received_at)

    def _publish(
        self, language: str, text: str, is_final: bool,
        raw_received_at: float | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc)
        self._last_caption_at = timestamp
        caption = CaptionEvent(
            stage_id=self.config.stage_id,
            language=language,
            text=self._terminology.apply(language, text),
            is_final=is_final,
            timestamp=timestamp,
            provider=self.provider.name,
        )
        self.bus.publish(caption)
        if self.diagnostics is not None:
            published_at = self.diagnostics.record(
                "bus_publish", language=language, is_final=is_final,
                text=caption.text,
            )
            if raw_received_at is not None:
                self.diagnostics.record(
                    "provider_to_bus", language=language,
                    seconds=published_at - raw_received_at,
                )

    async def _run(self) -> None:
        try:
            await self.provider.run(self.audio, self._on_transcript, self._connected)
            for language, assembler in self._assemblers.items():
                for unit in assembler.flush():
                    self._publish(language, unit.text, unit.is_final)
            self._state = "completed"
        except asyncio.CancelledError:
            self._state = "stopped"
            raise
        except Exception as exc:
            self._state = "failed"
            detail = str(exc).replace(self._api_key, "[REDACTED]").strip()
            handle = getattr(self.provider, "_resume_handle", None)
            if handle:
                detail = detail.replace(handle, "[REDACTED_HANDLE]")
            self._error = detail or exc.__class__.__name__
            if self.diagnostics is not None:
                self.diagnostics.record("stage_error", detail=self._error)
