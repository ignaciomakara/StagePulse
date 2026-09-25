"""Run one real 60-second file benchmark with optional diagnostic trace."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stagepulse.manager import StageManager
from stagepulse.models import StageConfig


async def main() -> None:
    key = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")
    config = StageConfig(
        "main", "Main Stage", "en", "es",
        ROOT / "samples/nerdearla-freedos-60s.wav",
        {"en": {"Word Perfect": "WordPerfect"},
         "es": {"Word Perfect": "WordPerfect"}},
    )
    manager = StageManager([config], key, diagnostics=True)
    manager.start("main")
    await manager.wait_all()
    status = manager.status("main")
    print(f"RESULT state={status.state} error={status.error!r} "
          f"connections={status.connections} reconnects={status.reconnect_count}", flush=True)
    if status.state != "completed":
        raise RuntimeError("File benchmark did not complete")


if __name__ == "__main__":
    asyncio.run(main())
