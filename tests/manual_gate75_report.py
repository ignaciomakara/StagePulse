"""Summarize monotonic diagnostic events from a real Gate 7.5 run."""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def summary(path: Path) -> dict:
    content = path.read_bytes()
    if content.startswith(b"\xff\xfe"):
        decoded = content.decode("utf-16")
    else:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            decoded = content.decode("cp1252")
    events = [
        json.loads(line[5:]) for line in decoded.splitlines()
        if line.startswith("DIAG ")
    ]
    pcm = [item for item in events if item["event"] == "pcm"]
    first = pcm[0]["at"] if pcm else None
    pcm_gaps = [b["at"] - a["at"] for a, b in zip(pcm, pcm[1:])]
    result: dict = {
        "log": str(path),
        "pcm_chunks": len(pcm),
        "pcm_bytes": sum(item["bytes"] for item in pcm),
        "pcm_span_s": round(pcm[-1]["at"] - first, 3) if first else None,
        "pcm_gap_max_s": round(max(pcm_gaps), 3) if pcm_gaps else None,
        "errors": [item["detail"] for item in events if item["event"] == "stage_error"],
    }
    for language in ("en", "es"):
        raw = [item for item in events if item["event"] == "provider_raw" and item["language"] == language]
        assembler = [item for item in events if item["event"] == "assembler" and item["language"] == language]
        published = [item for item in events if item["event"] == "bus_publish" and item["language"] == language]
        causal = [item["seconds"] for item in events if item["event"] == "provider_to_bus" and item["language"] == language]
        gaps = [b["at"] - a["at"] for a, b in zip(published, published[1:])]
        raw_gaps = [b["at"] - a["at"] for a, b in zip(raw, raw[1:])]
        longest_raw = max(zip(raw_gaps, raw, raw[1:]), default=None, key=lambda item: item[0])
        longest_pub = max(zip(gaps, published, published[1:]), default=None, key=lambda item: item[0])
        result[language] = {
            "first_raw_s": round(raw[0]["at"] - first, 3) if raw and first else None,
            "first_published_s": round(published[0]["at"] - first, 3) if published and first else None,
            "raw_count": len(raw),
            "raw_gap_max_s": round(max(raw_gaps), 3) if raw_gaps else None,
            "raw_gap_max_window_s": [
                round(longest_raw[1]["at"] - first, 3),
                round(longest_raw[2]["at"] - first, 3),
            ] if longest_raw and first else None,
            "other_raw_events_during_max_gap": sum(
                item["event"] == "provider_raw"
                and item["language"] != language
                and longest_raw[1]["at"] < item["at"] < longest_raw[2]["at"]
                for item in events
            ) if longest_raw else None,
            "pcm_chunks_during_max_raw_gap": sum(
                item["event"] == "pcm"
                and longest_raw[1]["at"] < item["at"] < longest_raw[2]["at"]
                for item in events
            ) if longest_raw else None,
            "assembler_count": len(assembler),
            "assembler_decisions": dict(Counter(item["decision"] for item in assembler)),
            "published_preview": sum(not item["is_final"] for item in published),
            "published_final": sum(item["is_final"] for item in published),
            "published_chars": sum(len(item["text"]) for item in published),
            "published_contains_dos": any("dos" in item["text"].casefold() for item in published),
            "published_contains_wordperfect": any(
                "wordperfect" in item["text"].casefold() for item in published
            ),
            "gap_median_s": round(statistics.median(gaps), 3) if gaps else None,
            "gap_max_s": round(max(gaps), 3) if gaps else None,
            "gap_max_window_s": [
                round(longest_pub[1]["at"] - first, 3),
                round(longest_pub[2]["at"] - first, 3),
            ] if longest_pub and first else None,
            "raw_during_max_pub_gap": sum(
                longest_pub[1]["at"] < item["at"] < longest_pub[2]["at"]
                for item in raw
            ) if longest_pub else None,
            "assembler_decisions_during_max_pub_gap": dict(Counter(
                item["decision"] for item in assembler
                if longest_pub[1]["at"] < item["at"] < longest_pub[2]["at"]
            )) if longest_pub else None,
            "provider_to_bus_median_ms": round(statistics.median(causal) * 1000, 3) if causal else None,
            "first_raw_text": raw[0]["text"] if raw else None,
            "first_published_text": published[0]["text"] if published else None,
        }
    en_first = result["en"]["first_published_s"]
    es_first = result["es"]["first_published_s"]
    result["first_es_minus_first_en_s"] = (
        round(es_first - en_first, 3) if en_first is not None and es_first is not None else None
    )
    return result


if __name__ == "__main__":
    names = [name for name in sys.argv[1:] if name != "--table"]
    if "--table" in sys.argv:
        print("| Run | Raw EN/ES | First raw EN/ES | First pub EN/ES | Pub preview EN/ES | Pub final EN/ES | Gap median EN/ES | Gap max EN/ES | Chars EN/ES |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for name in names:
            data = summary(Path(name))
            en, es = data["en"], data["es"]
            print(f"| {Path(name).stem} | {en['raw_count']}/{es['raw_count']} | "
                  f"{en['first_raw_s']}/{es['first_raw_s']} | "
                  f"{en['first_published_s']}/{es['first_published_s']} | "
                  f"{en['published_preview']}/{es['published_preview']} | "
                  f"{en['published_final']}/{es['published_final']} | "
                  f"{en['gap_median_s']}/{es['gap_median_s']} | "
                  f"{en['gap_max_s']}/{es['gap_max_s']} | "
                  f"{en['published_chars']}/{es['published_chars']} |")
    else:
        for name in names:
            print(json.dumps(summary(Path(name)), ensure_ascii=False, indent=2))
