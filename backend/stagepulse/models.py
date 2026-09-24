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
