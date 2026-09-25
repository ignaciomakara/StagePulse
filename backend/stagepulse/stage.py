"""Independent lifecycle for a single configured stage."""

from __future__ import annotations

import asyncio
import math
import time
from array import array
from collections import deque
from datetime import datetime, timezone

from .audio import AudioSource
from .captions import CaptionAssembler, CaptionBus
from .diagnostics import StageDiagnostics
from .models import CaptionEvent, StageConfig, StageStatus
from .providers import CaptionProvider, ProviderTranscript
from .terminology import TerminologyNormalizer


DEFAULT_TRANSLATION_STALL_SECONDS = 6.0
AUDIO_RECENT_SECONDS = 1.5
EN_RECENT_SECONDS = 2.5
EN_WINDOW_SECONDS = 3.0
RECOVERY_COOLDOWN_SECONDS = 30.0


class StageWorker:
    def __init__(
        self,
        config: StageConfig,
        audio: AudioSource,
        provider: CaptionProvider,
        bus: CaptionBus,
        api_key: str,
        diagnostics: StageDiagnostics | None = None,
        recover_translation_stall: bool = False,
        translation_stall_seconds: float = DEFAULT_TRANSLATION_STALL_SECONDS,
    ) -> None:
        self.config = config
        self.audio = audio
        self.provider = provider
        self.bus = bus
        self._api_key = api_key
        self.diagnostics = diagnostics
        self.recover_translation_stall = recover_translation_stall
        self.translation_stall_seconds = translation_stall_seconds
        self._state = "created"
        self._error: str | None = None
        self._connections = 0
        self._task: asyncio.Task | None = None
        self._assemblers: dict[str, CaptionAssembler] = {}
        self._terminology = TerminologyNormalizer(config.terminology)
        self._session_started_at: datetime | None = None
        self._connection_started_at: datetime | None = None
        self._last_caption_at: datetime | None = None
        self._last_pcm_at: float | None = None
        self._last_raw_en_at: float | None = None
        self._first_raw_en_at: float | None = None
        self._last_raw_es_at: float | None = None
        self._audio_ended = False
        self._pcm_chunks = 0
        self._pcm_bytes = 0
        self._en_since_es = 0
        self._recent_en: deque[float] = deque()
        self._stall_active = False
        self._stall_started_at: datetime | None = None
        self._stall_count = 0
        self._recovery_attempted = False
        self._recovery_requested_at: float | None = None

    @property
    def status(self) -> StageStatus:
        now = time.monotonic()
        active = self._stall_active and self._stall_evidence(now)
        translation_status = None
        if active:
            translation_status = "delayed"
        elif (
            self.config.target_language
            and self._state == "running"
            and getattr(self.provider, "provider_status", None) == "connected"
            and not self._audio_ended
            and self._last_pcm_at is not None
            and now - self._last_pcm_at <= AUDIO_RECENT_SECONDS
            and self._last_raw_en_at is not None
            and now - self._last_raw_en_at <= EN_RECENT_SECONDS
            and self._last_raw_es_at is not None
            and now - self._last_raw_es_at < self.translation_stall_seconds
        ):
            translation_status = "ok"
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
            translation_stall_active=active,
            translation_stall_started_at=self._stall_started_at if active else None,
            translation_stall_count=self._stall_count,
            age_last_raw_en=(
                round(now - self._last_raw_en_at, 2)
                if self._last_raw_en_at is not None else None
            ),
            age_last_raw_es=(
                round(now - self._last_raw_es_at, 2)
                if self._last_raw_es_at is not None else None
            ),
            translation_status=translation_status,
            translation_stall_reconnect_count=getattr(
                self.provider, "translation_stall_reconnect_count", 0
            ),
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
        self._last_pcm_at = None
        self._last_raw_en_at = None
        self._first_raw_en_at = None
        self._last_raw_es_at = None
        self._audio_ended = False
        self._pcm_chunks = 0
        self._pcm_bytes = 0
        self._en_since_es = 0
        self._recent_en.clear()
        self._stall_active = False
        self._stall_started_at = None
        self._stall_count = 0
        self._recovery_attempted = False
        self._recovery_requested_at = None
        self.bus.clear_latest(self.config.stage_id)
        if hasattr(self.audio, "on_chunk"):
            self.audio.on_chunk = self._audio_received
            self.audio.on_delivery = self._audio_delivered
            self.audio.on_end = self._audio_finished
        self._task = asyncio.create_task(self._run())

    def _audio_received(self, pcm: bytes, queue_depth: int) -> None:
        now = time.monotonic()
        interval = now - self._last_pcm_at if self._last_pcm_at is not None else None
        self._last_pcm_at = now
        self._pcm_chunks += 1
        self._pcm_bytes += len(pcm)
        if self.diagnostics is not None:
            samples = array("h")
            samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
            rms = (
                math.sqrt(sum(value * value for value in samples) / len(samples))
                if samples else 0.0
            )
            self.diagnostics.record(
                "pcm", bytes=len(pcm), interval_s=interval,
                cumulative_bytes=self._pcm_bytes, chunks=self._pcm_chunks,
                queue_depth=queue_depth, rms=rms,
            )

    def _audio_delivered(self, pcm: bytes, wait_seconds: float, queue_depth: int) -> None:
        if self.diagnostics is not None:
            self.diagnostics.record(
                "source_delivery", bytes=len(pcm),
                source_wait_s=wait_seconds, queue_depth=queue_depth,
            )

    def _audio_finished(self) -> None:
        self._audio_ended = True
        self._stall_active = False
        self._stall_started_at = None
        if self.diagnostics is not None:
            self.diagnostics.record("audio_end", cumulative_bytes=self._pcm_bytes)

    def _stall_evidence(self, now: float) -> bool:
        spanish_reference = (
            self._last_raw_es_at
            if self._last_raw_es_at is not None else self._first_raw_en_at
        )
        return bool(
            self.config.target_language == "es"
            and self._state == "running"
            and getattr(self.provider, "provider_status", None) == "connected"
            and not self._audio_ended
            and self._last_pcm_at is not None
            and now - self._last_pcm_at <= AUDIO_RECENT_SECONDS
            and self._last_raw_en_at is not None
            and now - self._last_raw_en_at <= EN_RECENT_SECONDS
            and spanish_reference is not None
            and now - spanish_reference >= self.translation_stall_seconds
            and self._en_since_es >= 2
            and len(self._recent_en) >= 2
        )

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
        if self.diagnostics is not None:
            self.diagnostics.record(
                "provider_connected", connections=self._connections,
                reconnects=getattr(self.provider, "reconnect_count", 0),
            )

    def _on_transcript(self, fragment: ProviderTranscript) -> None:
        assembler = self._assemblers.setdefault(fragment.language, CaptionAssembler())
        now = time.monotonic()
        if fragment.kind != "boundary":
            if fragment.language == "en":
                if self._first_raw_en_at is None:
                    self._first_raw_en_at = now
                self._last_raw_en_at = now
                self._en_since_es += 1
                self._recent_en.append(now)
                while self._recent_en and now - self._recent_en[0] > EN_WINDOW_SECONDS:
                    self._recent_en.popleft()
            elif fragment.language == "es":
                if self._stall_active and self.diagnostics is not None:
                    self.diagnostics.record(
                        "translation_stall_resolved",
                        gap_s=now - (
                            self._last_raw_es_at
                            if self._last_raw_es_at is not None else self._first_raw_en_at
                        ),
                        connections=self._connections,
                        reconnects=getattr(self.provider, "reconnect_count", 0),
                    )
                if self._recovery_requested_at is not None and self.diagnostics is not None:
                    self.diagnostics.record(
                        "translation_after_recovery",
                        seconds_since_request=now - self._recovery_requested_at,
                    )
                self._recovery_requested_at = None
                self._stall_active = False
                self._stall_started_at = None
                self._last_raw_es_at = now
                self._en_since_es = 0
                self._recent_en.clear()
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
        if fragment.language == "en" and fragment.kind != "boundary":
            try:
                asyncio.get_running_loop().call_soon(self._check_translation_stall, now)
            except RuntimeError:
                self._check_translation_stall(now)

    def _check_translation_stall(self, now: float) -> None:
        if self._stall_active or not self._stall_evidence(now):
            return
        self._stall_active = True
        self._stall_started_at = datetime.now(timezone.utc)
        self._stall_count += 1
        if self.diagnostics is not None:
            self.diagnostics.record(
                "translation_stall_detected",
                age_raw_es_s=(
                    now - self._last_raw_es_at
                    if self._last_raw_es_at is not None else None
                ),
                age_first_raw_en_s=(
                    now - self._first_raw_en_at
                    if self._first_raw_en_at is not None else None
                ),
                en_since_es=self._en_since_es,
                connections=self._connections,
                reconnects=getattr(self.provider, "reconnect_count", 0),
            )
        request_reconnect = getattr(
            self.provider, "request_translation_stall_reconnect", None
        )
        if (
            self.recover_translation_stall
            and not self._recovery_attempted
            and request_reconnect is not None
            and (
                self._recovery_requested_at is None
                or now - self._recovery_requested_at >= RECOVERY_COOLDOWN_SECONDS
            )
            and request_reconnect()
        ):
            self._recovery_attempted = True
            self._recovery_requested_at = now
            if self.diagnostics is not None:
                self.diagnostics.record(
                    "translation_recovery_requested",
                    connections=self._connections,
                    reconnects=getattr(self.provider, "reconnect_count", 0),
                    handle_available=getattr(
                        self.provider, "latest_resumption_handle_available", False
                    ),
                )

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
            self._stall_active = False
        except asyncio.CancelledError:
            self._state = "stopped"
            self._stall_active = False
            raise
        except Exception as exc:
            self._state = "failed"
            self._stall_active = False
            detail = str(exc).replace(self._api_key, "[REDACTED]").strip()
            handle = getattr(self.provider, "_resume_handle", None)
            if handle:
                detail = detail.replace(handle, "[REDACTED_HANDLE]")
            self._error = detail or exc.__class__.__name__
            if self.diagnostics is not None:
                self.diagnostics.record("stage_error", detail=self._error)
