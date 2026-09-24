"""Compare two real Gemini translation paths on the fixed Gate 2 WAV."""

from __future__ import annotations

import asyncio
import argparse
from contextlib import suppress
import json
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "samples" / "nerdearla-freedos-60s.wav"
TRANSCRIBE_MODEL = "gemini-3.5-transcribe-live"
TEXT_TRANSLATION_MODEL = "gemini-3.5-flash-lite"
LIVE_TRANSLATION_MODEL = "gemini-3.5-live-translate-preview"
SAMPLE_RATE = 16_000
CHUNK_BYTES = 3_200
TAIL_CHUNKS = 15


def safe_error(error: Exception, api_key: str) -> str:
    detail = str(error).replace(api_key, "[REDACTED]").strip()
    return detail or error.__class__.__name__


async def translate_final(
    client: genai.Client,
    transcript: str,
    api_key: str,
    started_at: float,
    translations: list[dict],
) -> None:
    try:
        translated = ""
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=TEXT_TRANSLATION_MODEL,
            contents=transcript,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Translate this English speech transcript into natural Latin American "
                    "Spanish. Preserve proper nouns and technical terms, including DOS "
                    "and WordPerfect. Output only the translation."
                ),
                temperature=0,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        translated = (response.text or "").strip()
        translations.append(
            {
                "input": transcript,
                "translation": translated,
                "elapsed_s": round(time.monotonic() - started_at, 3),
            }
        )
        print(f"A Spanish final: {translated}", flush=True)
    except Exception as exc:
        translations.append({"input": transcript, "error": safe_error(exc, api_key)})


async def run_pipeline(name: str, api_key: str) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found on PATH")

    client = genai.Client(api_key=api_key)
    result = {
        "pipeline": name,
        "models": [],
        "first_original_interim_s": None,
        "first_original_final_s": None,
        "first_translation_s": None,
        "first_audio_send_to_eof_signal_including_tail_s": None,
        "input_event_count": 0,
        "output_event_count": 0,
        "input_finals": [],
        "translations": [],
        "errors": [],
    }
    timing: dict[str, float | None] = {"start": None}
    timing["eof"] = None
    translation_results: list[dict] = []
    translation_tasks: list[asyncio.Task] = []
    last_a_transcript: list[str | None] = [None]

    try:
        if name == "A":
            result["models"] = [TRANSCRIBE_MODEL, TEXT_TRANSLATION_MODEL]
            model = TRANSCRIBE_MODEL
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                input_audio_transcription=types.AudioTranscriptionConfig(
                    language_codes=["en-US"]
                ),
            )
        else:
            result["models"] = [LIVE_TRANSLATION_MODEL]
            model = LIVE_TRANSLATION_MODEL
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                translation_config=types.TranslationConfig(
                    target_language_code="es", echo_target_language=False
                ),
            )

        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-i",
            str(INPUT_FILE),
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

        async with client.aio.live.connect(model=model, config=config) as session:
            async def send_audio() -> None:
                stderr_task = asyncio.create_task(process.stderr.read())
                try:
                    while chunk := await process.stdout.read(CHUNK_BYTES):
                        if timing["start"] is None:
                            timing["start"] = time.monotonic()
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
                    # Matching finalization tail for both pipelines; source WAV stays unchanged.
                    for _ in range(TAIL_CHUNKS):
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(CHUNK_BYTES),
                                mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                            )
                        )
                    await session.send_realtime_input(audio_stream_end=True)
                    result["first_audio_send_to_eof_signal_including_tail_s"] = round(
                        time.monotonic() - timing["start"], 3
                    )
                    timing["eof"] = time.monotonic()
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                    if not stderr_task.done():
                        stderr_task.cancel()

            async def receive() -> None:
                async for response in session.receive():
                    if timing["eof"] is not None and time.monotonic() - timing["eof"] >= 15:
                        return
                    content = response.server_content
                    if not content:
                        continue
                    started_at = timing["start"]
                    elapsed = (
                        round(time.monotonic() - started_at, 3)
                        if started_at is not None
                        else None
                    )
                    interim = content.interim_input_transcription
                    final = content.input_transcription
                    if interim and interim.text:
                        result["input_event_count"] += 1
                        if result["first_original_interim_s"] is None:
                            result["first_original_interim_s"] = elapsed
                            print(f"{name} first original interim ({elapsed}s): {interim.text}", flush=True)
                    if final and final.text:
                        transcript = final.text.strip()
                        result["input_event_count"] += 1
                        result["input_finals"].append(transcript)
                        if result["first_original_final_s"] is None:
                            result["first_original_final_s"] = elapsed
                        print(f"{name} original final ({elapsed}s): {transcript}", flush=True)
                        if (
                            name == "A"
                            and started_at is not None
                            and transcript != last_a_transcript[0]
                        ):
                            last_a_transcript[0] = transcript
                            translation_tasks.append(
                                asyncio.create_task(
                                    translate_final(
                                        client,
                                        transcript,
                                        api_key,
                                        started_at,
                                        translation_results,
                                    )
                                )
                            )
                    output = content.output_transcription
                    if output and output.text:
                        result["output_event_count"] += 1
                        result["translations"].append(output.text.strip())
                        if result["first_translation_s"] is None:
                            result["first_translation_s"] = elapsed
                            print(f"B first Spanish translation ({elapsed}s): {output.text.strip()}", flush=True)
                        elif name == "B":
                            print(f"B Spanish translation: {output.text.strip()}", flush=True)

            sender = asyncio.create_task(send_audio())
            receiver = asyncio.create_task(receive())
            try:
                await sender
                try:
                    await asyncio.wait_for(asyncio.shield(receiver), timeout=15)
                except TimeoutError:
                    receiver.cancel()
                    with suppress(asyncio.CancelledError):
                        await receiver
            finally:
                receiver.cancel()

        if translation_tasks:
            await asyncio.gather(*translation_tasks)
        if name == "A":
            result["translation_details"] = translation_results
            result["errors"].extend(
                item["error"] for item in translation_results if item.get("error")
            )
            result["translations"] = [
                item.get("translation", "") for item in translation_results
            ]
            successful = [
                item["elapsed_s"]
                for item in translation_results
                if item.get("translation")
            ]
            result["first_translation_s"] = min(successful) if successful else None
            result["output_event_count"] = len(successful)
    except Exception as exc:
        result["errors"].append(safe_error(exc, api_key))
        if process := locals().get("process"):
            if process.returncode is None:
                process.kill()
                await process.wait()
    finally:
        await client.aio.aclose()

    source_text = " ".join(result["input_finals"]).casefold()
    translated_text = " ".join(result["translations"]).casefold()
    result["terms"] = {
        "DOS": {
            "english": "dos" in source_text,
            "spanish": "dos" in translated_text,
        },
        "WordPerfect": {
            "english_exact": "wordperfect" in source_text,
            "english_spaced_variant": "word perfect" in source_text,
            "spanish_exact": "wordperfect" in translated_text,
            "spanish_spaced_variant": "word perfect" in translated_text,
        },
    }
    return result


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", choices=("A", "B"))
    selected = parser.parse_args().pipeline
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is missing from the local .env file")
    if not INPUT_FILE.is_file():
        raise SystemExit(f"Required bake-off file is missing: {INPUT_FILE}")

    for name in (selected,) if selected else ("A", "B"):
        print(f"Starting pipeline {name} on {INPUT_FILE.name}", flush=True)
        report = await run_pipeline(name, api_key)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
