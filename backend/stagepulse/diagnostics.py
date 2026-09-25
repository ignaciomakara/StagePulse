"""Optional event trace for controlled caption benchmarks."""

from __future__ import annotations

import json
import time


class StageDiagnostics:
    """Emit monotonic timestamps without credentials, handles, or audio data."""

    def __init__(self, stage_id: str) -> None:
        self.stage_id = stage_id

    def record(self, event: str, **fields: object) -> float:
        at = time.monotonic()
        print(
            "DIAG " + json.dumps(
                {"stage": self.stage_id, "event": event, "at": at, **fields},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return at
