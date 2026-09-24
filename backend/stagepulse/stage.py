"""Independent lifecycle for a single configured stage."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .audio import AudioSource
from .captions import CaptionAssembler, CaptionBus
from .models import CaptionEvent, StageConfig, StageStatus
from .providers import CaptionProvider, ProviderTranscript


class StageWorker:
    def __init__(
        self,
        config: StageConfig,
        audio: AudioSource,
        provider: CaptionProvider,
        bus: CaptionBus,
        api_key: str,
    ) -> None:
        self.config = config
        self.audio = audio
        self.provider = provider
        self.bus = bus
        self._api_key = api_key
        self._state = "created"
        self._error: str | None = None
        self._connections = 0
        self._task: asyncio.Task | None = None
        self._assemblers: dict[str, CaptionAssembler] = {}

    @property
    def status(self) -> StageStatus:
        return StageStatus(
            stage_id=self.config.stage_id,
            name=self.config.name,
            state=self._state,
            provider=self.provider.name,
            connections=self._connections,
            error=self._error,
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            raise RuntimeError(f"Stage {self.config.stage_id} is already running")
        self._state = "starting"
        self._error = None
        self._assemblers = {}
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
        self._state = "running"

    def _on_transcript(self, fragment: ProviderTranscript) -> None:
        assembler = self._assemblers.setdefault(fragment.language, CaptionAssembler())
        now = time.monotonic()
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
        for unit in units:
            self.bus.publish(
                CaptionEvent(
                    stage_id=self.config.stage_id,
                    language=fragment.language,
                    text=unit.text,
                    is_final=unit.is_final,
                    timestamp=datetime.now(timezone.utc),
                    provider=self.provider.name,
                )
            )

    async def _run(self) -> None:
        try:
            await self.provider.run(self.audio, self._on_transcript, self._connected)
            for language, assembler in self._assemblers.items():
                for unit in assembler.flush():
                    self.bus.publish(
                        CaptionEvent(
                            stage_id=self.config.stage_id,
                            language=language,
                            text=unit.text,
                            is_final=unit.is_final,
                            timestamp=datetime.now(timezone.utc),
                            provider=self.provider.name,
                        )
                    )
            self._state = "completed"
        except asyncio.CancelledError:
            self._state = "stopped"
            raise
        except Exception as exc:
            self._state = "failed"
            detail = str(exc).replace(self._api_key, "[REDACTED]").strip()
            self._error = detail or exc.__class__.__name__
