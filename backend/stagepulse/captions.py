"""Minimal caption assembly and in-process fan-out."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import AsyncIterator

from .models import CaptionEvent


@dataclass(frozen=True)
class CaptionUnit:
    text: str
    is_final: bool


class CaptionAssembler:
    """Combine short provider fragments for one language at one stage.

    A final unit is closed locally and will not be revised. Provider boundaries
    and final snapshots close units, as can punctuation, length, or elapsed time.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._started_at = 0.0
        self._last_preview_at = 0.0
        self._last_preview = ""
        self._last_fragment = ""
        self.last_decision = ""

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join(text.split())

    def fragment(self, text: str, now: float) -> list[CaptionUnit]:
        text = self._clean(text)
        if not text:
            self.last_decision = "empty_fragment"
            return []
        if text.casefold() == self._last_fragment.casefold():
            self.last_decision = "duplicate_fragment"
            return []
        self._last_fragment = text
        if not self._buffer:
            self._started_at = now
        self._buffer = f"{self._buffer} {text}".strip()
        self._buffer = re.sub(r"\s+([,.;!?])", r"\1", self._buffer)
        return self._ready(now)

    def interim_snapshot(self, text: str, now: float) -> list[CaptionUnit]:
        text = self._clean(text)
        if not text:
            self.last_decision = "empty_interim"
            return []
        if not self._buffer:
            self._started_at = now
        self._buffer = text
        return self._preview(now)

    def final_snapshot(self, text: str) -> list[CaptionUnit]:
        self._buffer = self._clean(text)
        return self.flush()

    def flush(self) -> list[CaptionUnit]:
        text = self._buffer.strip()
        self._buffer = ""
        self._last_preview = ""
        self._last_fragment = ""
        self.last_decision = "final" if text else "empty_buffer"
        return [CaptionUnit(text, True)] if text else []

    def _ready(self, now: float) -> list[CaptionUnit]:
        text = self._buffer
        if re.search(r"[.!?][\"']?$", text) and len(text) >= 12:
            return self.flush()
        if len(text) >= 115 or (len(text) >= 50 and now - self._started_at >= 4):
            return self.flush()
        return self._preview(now)

    def _preview(self, now: float) -> list[CaptionUnit]:
        text = self._buffer
        if (
            len(text) >= 24
            and text != self._last_preview
            and (not self._last_preview or now - self._last_preview_at >= 1)
        ):
            self._last_preview = text
            self._last_preview_at = now
            self.last_decision = "preview"
            return [CaptionUnit(text, False)]
        if len(text) < 24:
            self.last_decision = "buffered_short"
        elif text == self._last_preview:
            self.last_decision = "unchanged_preview"
        else:
            self.last_decision = "preview_throttled"
        return []


class CaptionSubscription:
    def __init__(self, bus: CaptionBus, stage_id: str) -> None:
        self._bus = bus
        self.stage_id = stage_id
        self.queue: asyncio.Queue[CaptionEvent | None] = asyncio.Queue(maxsize=128)
        self.dropped_events = 0
        self._closed = False

    def _put(self, event: CaptionEvent | None) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped_events += 1
        self.queue.put_nowait(event)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unsubscribe(self)
            self._put(None)

    def __aiter__(self) -> AsyncIterator[CaptionEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[CaptionEvent]:
        while (event := await self.queue.get()) is not None:
            yield event


class CaptionBus:
    """Publish live events and seed new subscribers with each language's latest event."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[CaptionSubscription]] = defaultdict(set)
        self._latest: dict[str, dict[str, CaptionEvent]] = defaultdict(dict)

    def subscribe(self, stage_id: str) -> CaptionSubscription:
        subscription = CaptionSubscription(self, stage_id)
        for event in sorted(
            self._latest.get(stage_id, {}).values(), key=lambda item: item.timestamp
        ):
            subscription._put(event)
        self._subscribers[stage_id].add(subscription)
        return subscription

    def publish(self, event: CaptionEvent) -> None:
        self._latest[event.stage_id][event.language] = event
        for subscription in tuple(self._subscribers.get(event.stage_id, ())):
            subscription._put(event)

    def clear_latest(self, stage_id: str) -> None:
        """Prevent a restarted stage from replaying captions from its prior run."""
        self._latest.pop(stage_id, None)

    def subscriber_count(self, stage_id: str) -> int:
        return len(self._subscribers.get(stage_id, ()))

    def _unsubscribe(self, subscription: CaptionSubscription) -> None:
        self._subscribers[subscription.stage_id].discard(subscription)
