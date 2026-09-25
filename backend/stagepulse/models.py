"""Configuration and normalized events shared by StagePulse components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StageConfig:
    stage_id: str
    name: str
    source_language: str
    target_language: str | None
    audio_file: Path
    terminology: dict[str, dict[str, str]] | None = None


@dataclass(frozen=True)
class CaptionEvent:
    """A caption emitted by StagePulse for one stage and language.

    is_final means StagePulse closed this caption unit and will not revise it.
    It does not imply an explicit final signal from the provider: the assembler
    can also close a unit on punctuation, length, or elapsed time.
    """

    stage_id: str
    language: str
    text: str
    is_final: bool
    timestamp: datetime
    provider: str


@dataclass(frozen=True)
class StageStatus:
    stage_id: str
    name: str
    state: str
    provider: str
    connections: int
    error: str | None = None
    provider_status: str = "idle"
    reconnect_count: int = 0
    last_error: str | None = None
    last_provider_event: str | None = None
    last_provider_event_at: datetime | None = None
    latest_resumption_handle_available: bool = False
    last_audio_at: datetime | None = None
    last_caption_at: datetime | None = None
    session_started_at: datetime | None = None
    connection_started_at: datetime | None = None
    go_away_count: int = 0
    go_away_at: datetime | None = None
    go_away_time_left: str | None = None
    resumption_update_count: int = 0
    forced_reconnect_count: int = 0
    dropped_audio_bytes: int = 0
    translation_stall_active: bool = False
    translation_stall_started_at: datetime | None = None
    translation_stall_count: int = 0
    age_last_raw_en: float | None = None
    age_last_raw_es: float | None = None
    translation_status: str | None = None
    translation_stall_reconnect_count: int = 0
