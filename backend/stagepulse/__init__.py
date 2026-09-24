"""Small in-process core for StagePulse stages."""

from .manager import StageManager
from .models import CaptionEvent, StageConfig, StageStatus

__all__ = ["CaptionEvent", "StageConfig", "StageManager", "StageStatus"]
