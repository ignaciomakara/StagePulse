"""Run configured file-backed stages and print their caption events."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from stagepulse import StageManager
from stagepulse.stage import DEFAULT_TRANSLATION_STALL_SECONDS


ROOT = Path(__file__).resolve().parents[1]


async def run(
    config_path: Path,
    stop_stage: str | None,
    stop_after: float,
    debug_reconnect_after: float | None,
    diagnostics: bool = False,
    recover_translation_stall: bool = False,
    translation_stall_seconds: float = DEFAULT_TRANSLATION_STALL_SECONDS,
) -> int:
    api_key = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(f"GEMINI_API_KEY is missing from {ROOT / '.env'}")
    manager = StageManager.from_file(
        config_path, api_key, debug_reconnect_after=debug_reconnect_after,
        diagnostics=diagnostics,
        recover_translation_stall=recover_translation_stall,
        translation_stall_seconds=translation_stall_seconds,
    )
    if stop_stage is not None and stop_stage not in manager.workers:
        raise ValueError(f"Unknown stage: {stop_stage}")

    subscriptions = {}
    tasks = []
    counts: dict[str, Counter] = {}

    async def consume(label: str, subscription) -> None:
        counts[label] = Counter()
        async for event in subscription:
            key = f"{event.language}_{'final' if event.is_final else 'preview'}"
            counts[label][key] += 1
            count = counts[label][key]
            if event.is_final or count <= 3:
                print(
                    f"CAPTION at={event.timestamp.isoformat()} "
                    f"subscriber={label} stage={event.stage_id} "
                    f"language={event.language} final={event.is_final} "
                    f"text={event.text!r}",
                    flush=True,
                )

    for stage_id in manager.workers:
        subscription = manager.bus.subscribe(stage_id)
        subscriptions[stage_id] = subscription
        tasks.append(asyncio.create_task(consume(stage_id, subscription)))
    first_stage = next(iter(manager.workers))
    extra = manager.bus.subscribe(first_stage)
    subscriptions[f"{first_stage}-viewer-2"] = extra
    tasks.append(asyncio.create_task(consume(f"{first_stage}-viewer-2", extra)))

    async def monitor() -> None:
        previous = {}
        while True:
            for stage_id, status in manager.statuses().items():
                current = (
                    status.state,
                    status.provider_status,
                    status.connections,
                    status.reconnect_count,
                    status.go_away_count,
                    status.forced_reconnect_count,
                    status.latest_resumption_handle_available,
                    status.dropped_audio_bytes,
                )
                if current != previous.get(stage_id):
                    previous[stage_id] = current
                    print(
                        f"HEALTH at={datetime.now(timezone.utc).isoformat()} "
                        f"stage={stage_id} state={status.state} "
                        f"provider_status={status.provider_status} "
                        f"connections={status.connections} reconnects={status.reconnect_count} "
                        f"go_away={status.go_away_count} "
                        f"go_away_at={status.go_away_at} "
                        f"time_left={status.go_away_time_left} "
                        f"forced={status.forced_reconnect_count} "
                        f"handle_available={status.latest_resumption_handle_available} "
                        f"dropped_audio_bytes={status.dropped_audio_bytes} "
                        f"last_event={status.last_provider_event}",
                        flush=True,
                    )
            await asyncio.sleep(0.5)

    manager.start_all()
    monitor_task = asyncio.create_task(monitor())
    stopper = None
    if stop_stage is not None:
        async def stop_later() -> None:
            await asyncio.sleep(stop_after)
            await manager.stop(stop_stage)
            print(f"STOPPED stage={stop_stage}", flush=True)

        stopper = asyncio.create_task(stop_later())
    try:
        await manager.wait_all()
        if stopper is not None and not stopper.done():
            stopper.cancel()
            try:
                await stopper
            except asyncio.CancelledError:
                pass
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        for subscription in subscriptions.values():
            subscription.close()
        await asyncio.gather(*tasks)

    for stage_id, status in manager.statuses().items():
        print(f"STATUS {json.dumps(asdict(status), ensure_ascii=False, default=str)}")
    for label, count in counts.items():
        print(f"COUNTS subscriber={label} {json.dumps(count, sort_keys=True)}")
    return 1 if any(status.state == "failed" for status in manager.statuses().values()) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "stages.gate3.json"
    )
    parser.add_argument("--stop-stage", help="Stop one configured stage independently")
    parser.add_argument("--diagnostics", action="store_true", help="Emit benchmark trace with caption text")
    parser.add_argument(
        "--recover-translation-stall", action="store_true",
        help="Debug only: attempt one reconnect on confirmed translation stall",
    )
    parser.add_argument(
        "--translation-stall-seconds", type=float,
        default=DEFAULT_TRANSLATION_STALL_SECONDS,
    )
    parser.add_argument("--stop-after", type=float, default=20.0)
    parser.add_argument(
        "--debug-reconnect-after",
        type=float,
        help="Test only: force one provider reconnect after this many seconds",
    )
    args = parser.parse_args()
    if args.stop_after <= 0:
        parser.error("--stop-after must be positive")
    try:
        return asyncio.run(
            run(
                args.config,
                args.stop_stage,
                args.stop_after,
                args.debug_reconnect_after,
                args.diagnostics,
                args.recover_translation_stall,
                args.translation_stall_seconds,
            )
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
