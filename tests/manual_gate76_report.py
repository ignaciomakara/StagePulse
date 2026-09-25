"""Summarize real Gate 7.6 diagnostic traces without inventing speech timing."""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, deque
from pathlib import Path


def load_events(path: Path) -> list[dict]:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe"):
        content = data.decode("utf-16")
    else:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = data.decode("cp1252")
    return [json.loads(line[5:]) for line in content.splitlines() if line.startswith("DIAG ")]


def distribution(values: list[float], scale: float = 1.0) -> dict | None:
    if not values:
        return None
    ordered = sorted(value * scale for value in values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
        "max": round(ordered[-1], 3),
    }


def retrospective_stalls(
    events: list[dict], threshold: float = 6.0, require_two_recent: bool = True
) -> list[dict]:
    """Apply timing conditions to old raw logs; this is not a live detection test."""
    first_pcm = None
    last_pcm = None
    last_es = None
    recent_en: deque[float] = deque()
    en_since_es = 0
    active = False
    found = []
    for item in events:
        at = item["at"]
        if item["event"] == "pcm":
            first_pcm = at if first_pcm is None else first_pcm
            last_pcm = at
        if item["event"] != "provider_raw":
            continue
        if item["language"] == "es":
            last_es = at
            recent_en.clear()
            en_since_es = 0
            active = False
        elif item["language"] == "en":
            en_since_es += 1
            recent_en.append(at)
            while recent_en and at - recent_en[0] > 3.0:
                recent_en.popleft()
            if (
                not active and last_es is not None and last_pcm is not None
                and at - last_es >= threshold and at - last_pcm <= 1.5
                and (len(recent_en) >= 2 if require_two_recent else en_since_es >= 2)
            ):
                active = True
                found.append({
                    "at_from_first_pcm_s": round(at - first_pcm, 3),
                    "age_raw_es_s": round(at - last_es, 3),
                    "recent_en_count": len(recent_en),
                })
    return found


def summary(path: Path) -> dict:
    events = load_events(path)
    pcm = [item for item in events if item["event"] == "pcm"]
    delivery = [item for item in events if item["event"] == "source_delivery"]
    sends = [item for item in events if item["event"] == "provider_send"]
    raw_en = [item for item in events if item["event"] == "provider_raw" and item["language"] == "en"]
    raw_es = [item for item in events if item["event"] == "provider_raw" and item["language"] == "es"]
    receipt_by_offset = {item["cumulative_bytes"]: item["at"] for item in pcm}
    matched = [
        item["started_at"] - receipt_by_offset[item["cumulative_bytes"]]
        for item in sends
        if item["dropped_bytes"] == 0 and item["cumulative_bytes"] in receipt_by_offset
    ]
    first_pcm = pcm[0]["at"] if pcm else None
    first_rms_over_100 = next((item["at"] for item in pcm if item["rms"] > 100), None)
    rms_by_second: dict[int, list[float]] = {}
    rms_by_ten_seconds: dict[int, list[float]] = {}
    if first_pcm is not None:
        for item in pcm:
            second = int(item["at"] - first_pcm)
            rms_by_ten_seconds.setdefault(second // 10 * 10, []).append(item["rms"])
            if second < 8:
                rms_by_second.setdefault(second, []).append(item["rms"])
    result = {
        "log": str(path),
        "retrospective_stalls": retrospective_stalls(events),
        "retrospective_stalls_two_since_es": retrospective_stalls(events, require_two_recent=False),
        "pcm_chunks": len(pcm),
        "pcm_bytes": sum(item["bytes"] for item in pcm),
        "pcm_chunk_sizes": dict(Counter(item["bytes"] for item in pcm)),
        "first_eight_seconds_pcm_rms_median": {
            second: round(statistics.median(values), 1)
            for second, values in rms_by_second.items()
        },
        "pcm_rms_median_by_ten_seconds": {
            second: round(statistics.median(values), 1)
            for second, values in rms_by_ten_seconds.items()
        },
        "first_pcm_rms_over_100_s": round(first_rms_over_100 - first_pcm, 3) if first_rms_over_100 is not None and first_pcm is not None else None,
        "pcm_interval_ms": distribution(
            [item["interval_s"] for item in pcm if item["interval_s"] is not None], 1000
        ),
        "pcm_queue_depth": distribution([item["queue_depth"] for item in pcm]),
        "source_wait_ms": distribution([item["source_wait_s"] for item in delivery], 1000),
        "provider_buffer_wait_ms": distribution([item["buffer_wait_s"] for item in sends], 1000),
        "provider_send_duration_ms": distribution([item["send_duration_s"] for item in sends], 1000),
        "receipt_to_send_ms": distribution(matched, 1000),
        "matched_send_chunks": len(matched),
        "provider_sent_chunks": len(sends),
        "raw_en_count": len(raw_en),
        "raw_es_count": len(raw_es),
        "max_raw_es_gap_during_pcm_s": round(max(
            (b["at"] - a["at"] for a, b in zip(raw_es, raw_es[1:])
             if pcm and b["at"] <= pcm[-1]["at"]), default=0.0
        ), 3) if len(raw_es) > 1 else None,
        "first_pcm_to_first_send_s": round(sends[0]["started_at"] - first_pcm, 3) if sends and first_pcm else None,
        "first_pcm_to_first_raw_en_s": round(next(item["at"] for item in events if item["event"] == "provider_raw" and item["language"] == "en") - first_pcm, 3) if first_pcm and any(item["event"] == "provider_raw" and item["language"] == "en" for item in events) else None,
        "first_pcm_to_first_raw_es_s": round(next(item["at"] for item in events if item["event"] == "provider_raw" and item["language"] == "es") - first_pcm, 3) if first_pcm and any(item["event"] == "provider_raw" and item["language"] == "es" for item in events) else None,
        "stall_events": [
            {"event": item["event"], "at_from_first_pcm_s": round(item["at"] - first_pcm, 3),
             **{key: value for key, value in item.items() if key not in {"stage", "event", "at"}}}
            for item in events if item["event"].startswith("translation_") and first_pcm
        ],
        "connection_events": [
            {"at_from_first_pcm_s": round(item["at"] - first_pcm, 3),
             "connections": item["connections"], "reconnects": item["reconnects"]}
            for item in events if item["event"] == "provider_connected" and first_pcm
        ],
    }
    return result


if __name__ == "__main__":
    retrospective = "--retrospective" in sys.argv
    table = "--table" in sys.argv
    names = [arg for arg in sys.argv[1:] if arg not in {"--retrospective", "--table"}]
    if table:
        print("| Run | PCM chunks | First send | First raw EN/ES | Raw EN/ES | Max live ES raw gap | Receipt-to-send median/p95 ms | Stalls | Connections |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in names:
        result = (
            {
                "log": name,
                "retrospective_stalls_two_recent": retrospective_stalls(load_events(Path(name))),
                "retrospective_stalls_two_since_es": retrospective_stalls(
                    load_events(Path(name)), require_two_recent=False
                ),
            }
            if retrospective else summary(Path(name))
        )
        if table:
            latency = result["receipt_to_send_ms"]
            print(f"| {Path(name).stem} | {result['pcm_chunks']} | "
                  f"{result['first_pcm_to_first_send_s']} | "
                  f"{result['first_pcm_to_first_raw_en_s']}/{result['first_pcm_to_first_raw_es_s']} | "
                  f"{result['raw_en_count']}/{result['raw_es_count']} | "
                  f"{result['max_raw_es_gap_during_pcm_s']} | "
                  f"{latency['median']}/{latency['p95']} | "
                  f"{sum(item['event'] == 'translation_stall_detected' for item in result['stall_events'])} | "
                  f"{len(result['connection_events'])} |")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
