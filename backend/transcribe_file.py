"""Stream a local media file to Gemini Live and print its transcription."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


MODEL = "gemini-3.5-transcribe-live"
SAMPLE_RATE = 16_000
CHUNK_BYTES = 3_200  # 100 ms of mono, 16-bit PCM.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class InputError(Exception):
    """The input file or FFmpeg could not be used."""


class GeminiError(Exception):
    """Gemini Live connection, model, or streaming failure."""


async def stream_file(path: Path, api_key: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise InputError("FFmpeg was not found. Install FFmpeg and add it to PATH.")
    if not path.is_file():
        raise InputError(f"Input file does not exist or is not a file: {path}")

    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-i",
            str(path),
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
    except OSError as exc:
        raise InputError(f"Could not start FFmpeg: {exc}") from None

    client = genai.Client(api_key=api_key)
    config = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=[]),
    )
    final_received = asyncio.Event()

    async def send_audio(session: object) -> None:
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            while chunk := await process.stdout.read(CHUNK_BYTES):
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )
            return_code = await process.wait()
            stderr = (await stderr_task).decode(errors="replace").strip()
            if return_code:
                detail = f": {stderr}" if stderr else ""
                raise InputError(f"FFmpeg could not decode the input file{detail}")
            # Give VAD a short real-time pause to finalize speech at EOF.
            for _ in range(15):
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=bytes(CHUNK_BYTES),
                        mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                    )
                )
            await session.send_realtime_input(audio_stream_end=True)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not stderr_task.done():
                stderr_task.cancel()

    async def receive_transcripts(session: object) -> None:
        async for response in session.receive():
            content = response.server_content
            if not content:
                continue
            interim = content.interim_input_transcription
            final = content.input_transcription
            if interim and interim.text:
                print(f"Interim: {interim.text}", flush=True)
            if final and final.text:
                print(f"Final: {final.text}\n", flush=True)
                final_received.set()

    try:
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            receiver = asyncio.create_task(receive_transcripts(session))
            final_waiter = asyncio.create_task(final_received.wait())
            try:
                await send_audio(session)
                done, _ = await asyncio.wait(
                    {receiver, final_waiter},
                    timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receiver in done:
                    receiver.result()
                if not final_received.is_set():
                    raise GeminiError(
                        "Gemini did not finish the transcription after the audio stream ended."
                    )
            finally:
                receiver.cancel()
                final_waiter.cancel()
    except InputError:
        raise
    except GeminiError:
        raise
    except Exception as exc:
        detail = str(exc).replace(api_key, "[REDACTED]").strip()
        detail = detail or exc.__class__.__name__
        raise GeminiError(f"Gemini Live ({MODEL}) failed: {detail}") from None
    finally:
        await client.aio.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe a local audio or video file with Gemini Live."
    )
    parser.add_argument("input_file", type=Path, help="Local audio or video file")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(
            "Error: GEMINI_API_KEY is missing. Add it to the project .env file.",
            file=sys.stderr,
        )
        return 2

    try:
        asyncio.run(stream_file(args.input_file.resolve(), api_key))
    except InputError as exc:
        print(f"Input/FFmpeg error: {exc}", file=sys.stderr)
        return 1
    except GeminiError as exc:
        print(f"Gemini API/model error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
