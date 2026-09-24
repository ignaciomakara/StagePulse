"""Measure interim translation with a small, rate-limited caption assembler."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "samples" / "nerdearla-freedos-60s.wav"
TRANSCRIBE_MODEL = "gemini-3.5-transcribe-live"
TRANSLATION_MODEL = "gemini-3.5-flash-lite"
SAMPLE_RATE = 16_000
CHUNK_BYTES = 3_200
TAIL_CHUNKS = 15

# A Spanish result is usable for this experiment only if it is a phrase with at
# least four words and 24 non-space characters. Shorter results are logged but
# do not count toward first usable translation.
MIN_USABLE_WORDS = 4
MIN_USABLE_CHARACTERS = 24


@dataclass
class CaptionAssembler:
    """Rate-limit translation of changing interim snapshots without text diffing."""

    segment_started_at: float
    last_requested_text: str = ""
    last_request_at: float | None = None
    generation: int = 0

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.split())

    def should_translate(self, text: str, now: float) -> tuple[bool, str]:
        candidate = self.normalize(text)
        previous = self.normalize(self.last_requested_text)
        words = candidate.split()
        compact_length = len(re.sub(r"\s", "", candidate))

        if candidate == previous:
            return False, "duplicate"
        if len(words) < 5 or compact_length < 24:
            return False, "too_short"

        last_request = (
            self.last_request_at
            if self.last_request_at is not None
            else self.segment_started_at
        )
        elapsed = now - last_request
        growth = len(candidate) - len(previous)
        punctuation = bool(re.search(r"[,;:.!?][\"')\]]*$", candidate))

        if elapsed < 0.75:
            return False, "cooldown"
        if punctuation and len(words) >= 5 and compact_length >= 24:
            reason = "punctuation"
        elif len(words) >= 8 and compact_length >= 38 and growth >= 12:
            reason = "clause_length"
        elif elapsed >= 1.0 and growth >= 12:
            reason = "time_and_new_text"
        else:
            return False, "not_enough_new_text"

        self.last_requested_text = candidate
        self.last_request_at = now
        return True, reason

    def finalize_segment(self, now: float) -> int:
        """Invalidate stale interim results and prepare the next speech segment."""
        self.generation += 1
        self.segment_started_at = now
        self.last_requested_text = ""
        self.last_request_at = None
        return self.generation


def safe_error(error: Exception, api_key: str) -> str:
    detail = str(error).replace(api_key, "[REDACTED]").strip()
    return detail or error.__class__.__name__


def is_usable_translation(text: str) -> bool:
    words = text.split()
    compact_length = len(re.sub(r"\s", "", text))
    dangling_words = {
        "a", "al", "con", "de", "del", "el", "en", "es", "la", "las", "lo",
        "los", "o", "para", "por", "que", "se", "sin", "su", "sus", "un",
        "una", "unas", "uno", "unos", "y",
    }
    return (
        len(words) >= MIN_USABLE_WORDS
        and compact_length >= MIN_USABLE_CHARACTERS
        and words[-1].strip(".,;:!?\"'()[]{}").casefold() not in dangling_words
    ) if words else False


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_FILE,
        help="Audio file (defaults to the required Nerdearla 60-second WAV)",
    )
    args = parser.parse_args()
    input_file = args.input.resolve()

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is missing from the local .env file")
    if not input_file.is_file():
        raise SystemExit(f"Audio file not found: {input_file}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("FFmpeg was not found on PATH")

    client = genai.Client(api_key=api_key)
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US"]),
    )
    result: dict = {
        "pipeline": "A2",
        "models": [TRANSCRIBE_MODEL, TRANSLATION_MODEL],
        "input_file": str(input_file),
        "usable_translation_rule": {
            "minimum_words": MIN_USABLE_WORDS,
            "minimum_non_space_characters": MIN_USABLE_CHARACTERS,
            "rejects_dangling_final_connectors_or_determiners": True,
        },
        "first_interim_english_s": None,
        "first_final_english_s": None,
        "first_usable_provisional_spanish_s": None,
        "first_final_spanish_s": None,
        "provisional_requests": 0,
        "final_requests": 0,
        "provisional_usable_count": 0,
        "provisional_stale_discarded_count": 0,
        "interim_events": 0,
        "final_events": 0,
        "provisional_outputs": [],
        "final_outputs": [],
        "errors": [],
    }
    timing: dict[str, float | None] = {"audio_start": None, "audio_end": None}
    audio_started = asyncio.Event()
    assembler: CaptionAssembler | None = None
    translation_tasks: set[asyncio.Task] = set()
    provisional_in_flight = False
    provisional_times: list[float] = []
    last_final_text = ""
    generation = 0

    async def request_translation(text: str, kind: str, event_generation: int) -> None:
        nonlocal provisional_in_flight
        started_at = timing["audio_start"]
        assert started_at is not None
        if kind == "PROVISIONAL":
            provisional_in_flight = True
            result["provisional_requests"] += 1
        else:
            result["final_requests"] += 1

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=TRANSLATION_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Translate the English speech into natural Latin American Spanish. "
                        "Keep this translation faithful to the provided text, preserve "
                        "technical names exactly (including DOS and WordPerfect), and "
                        "output only the Spanish translation. The input may be an "
                        "incomplete live speech hypothesis."
                    ),
                    temperature=0,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            translated = (response.text or "").strip()
            elapsed = round(time.monotonic() - started_at, 3)
            if kind == "PROVISIONAL":
                # Do not display a provisional result that belongs to a segment
                # already superseded by a finalized transcript.
                if event_generation != generation:
                    result["provisional_stale_discarded_count"] += 1
                    return
                usable = is_usable_translation(translated)
                output = {
                    "english_snapshot": text,
                    "spanish": translated,
                    "elapsed_s": elapsed,
                    "usable": usable,
                }
                result["provisional_outputs"].append(output)
                if usable:
                    result["provisional_usable_count"] += 1
                    provisional_times.append(elapsed)
                    if result["first_usable_provisional_spanish_s"] is None:
                        result["first_usable_provisional_spanish_s"] = elapsed
                usability = "USABLE" if usable else "NOT USABLE (short fragment)"
                print(f"PROVISIONAL {usability} ({elapsed}s): {translated}", flush=True)
            else:
                output = {"english_final": text, "spanish": translated, "elapsed_s": elapsed}
                result["final_outputs"].append(output)
                if translated and result["first_final_spanish_s"] is None:
                    result["first_final_spanish_s"] = elapsed
                print(f"FINAL ({elapsed}s): {translated}", flush=True)
        except Exception as exc:
            result["errors"].append(
                {"kind": kind, "input": text, "error": safe_error(exc, api_key)}
            )
            print(f"{kind} translation error: {safe_error(exc, api_key)}", file=sys.stderr)
        finally:
            if kind == "PROVISIONAL":
                provisional_in_flight = False

    def schedule_translation(text: str, kind: str, event_generation: int) -> None:
        nonlocal provisional_in_flight
        if kind == "PROVISIONAL":
            provisional_in_flight = True
        task = asyncio.create_task(request_translation(text, kind, event_generation))
        translation_tasks.add(task)
        task.add_done_callback(translation_tasks.discard)

    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-i",
        str(input_file),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None

    try:
        async with client.aio.live.connect(model=TRANSCRIBE_MODEL, config=config) as session:
            async def send_audio() -> None:
                stderr_task = asyncio.create_task(process.stderr.read())
                try:
                    while chunk := await process.stdout.read(CHUNK_BYTES):
                        if timing["audio_start"] is None:
                            timing["audio_start"] = time.monotonic()
                            audio_started.set()
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=chunk,
                                mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                            )
                        )
                    return_code = await process.wait()
                    ffmpeg_error = (await stderr_task).decode(errors="replace").strip()
                    if return_code:
                        raise RuntimeError(
                            f"FFmpeg exited with status {return_code}: {ffmpeg_error}"
                        )
                    for _ in range(TAIL_CHUNKS):
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(CHUNK_BYTES),
                                mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                            )
                        )
                    await session.send_realtime_input(audio_stream_end=True)
                    timing["audio_end"] = time.monotonic()
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                    if not stderr_task.done():
                        stderr_task.cancel()

            async def receive() -> None:
                nonlocal assembler, generation, last_final_text
                await audio_started.wait()
                assert timing["audio_start"] is not None
                assembler = CaptionAssembler(segment_started_at=timing["audio_start"])
                async for response in session.receive():
                    content = response.server_content
                    if not content:
                        continue
                    now = time.monotonic()
                    elapsed = round(now - timing["audio_start"], 3)
                    interim = content.interim_input_transcription
                    final = content.input_transcription

                    if interim and interim.text and not (final and final.text):
                        interim_text = assembler.normalize(interim.text)
                        result["interim_events"] += 1
                        if result["first_interim_english_s"] is None:
                            result["first_interim_english_s"] = elapsed
                            print(f"FIRST INTERIM ENGLISH ({elapsed}s): {interim_text}", flush=True)
                        if not provisional_in_flight:
                            should_send, reason = assembler.should_translate(interim_text, now)
                            if should_send:
                                print(f"Request provisional ({reason}): {interim_text}", flush=True)
                                schedule_translation(interim_text, "PROVISIONAL", generation)

                    if final and final.text:
                        final_text = final.text.strip()
                        result["final_events"] += 1
                        if result["first_final_english_s"] is None:
                            result["first_final_english_s"] = elapsed
                        print(f"ENGLISH FINAL ({elapsed}s): {final_text}", flush=True)
                        if final_text != last_final_text:
                            last_final_text = final_text
                            generation = assembler.finalize_segment(now)
                            schedule_translation(final_text, "FINAL", generation)

            sender = asyncio.create_task(send_audio())
            receiver = asyncio.create_task(receive())
            try:
                await sender
                try:
                    await asyncio.wait_for(asyncio.shield(receiver), timeout=12)
                except TimeoutError:
                    receiver.cancel()
                    with suppress(asyncio.CancelledError):
                        await receiver
            finally:
                receiver.cancel()

        if translation_tasks:
            await asyncio.gather(*tuple(translation_tasks), return_exceptions=True)
    except Exception as exc:
        result["errors"].append({"kind": "pipeline", "error": safe_error(exc, api_key)})
        print(f"A2 pipeline error: {safe_error(exc, api_key)}", file=sys.stderr)
        if process.returncode is None:
            process.kill()
            await process.wait()
    finally:
        await client.aio.aclose()

    all_english = " ".join(item["english_final"] for item in result["final_outputs"]).casefold()
    provisional_spanish = " ".join(
        item["spanish"] for item in result["provisional_outputs"]
    ).casefold()
    final_spanish = " ".join(item["spanish"] for item in result["final_outputs"]).casefold()
    result["terms"] = {
        term: {
            "english_final_exact": term.casefold() in all_english,
            "spanish_provisional_exact": term.casefold() in provisional_spanish,
            "spanish_final_exact": term.casefold() in final_spanish,
        }
        for term in ("DOS", "WordPerfect")
    }
    result["flash_lite_requests_total"] = (
        result["provisional_requests"] + result["final_requests"]
    )
    result["provisional_usable_elapsed_s"] = provisional_times
    result["first_audio_send_to_eof_signal_including_tail_s"] = (
        round(timing["audio_end"] - timing["audio_start"], 3)
        if timing["audio_end"] is not None and timing["audio_start"] is not None
        else None
    )
    print("A2 SUMMARY:", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
