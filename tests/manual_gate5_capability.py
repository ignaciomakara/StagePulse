"""Probe Live Translate resumption and compression with real local audio.

Never prints the API key or resumption handles. Run from the project root.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import dotenv_values
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.audio import FileAudioSource


ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.5-live-translate-preview"


async def probe_connection(client, handle: str | None) -> str | None:
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        translation_config=types.TranslationConfig(target_language_code="es"),
        session_resumption=types.SessionResumptionConfig(handle=handle),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    )
    latest_handle = handle
    updates = 0
    original = 0
    translated = 0
    go_away = 0
    async with client.aio.live.connect(model=MODEL, config=config) as session:
        async def receive() -> None:
            nonlocal latest_handle, updates, original, translated, go_away
            async for response in session.receive():
                update = response.session_resumption_update
                if update:
                    updates += 1
                    if update.resumable and update.new_handle:
                        latest_handle = update.new_handle
                if response.go_away:
                    go_away += 1
                if response.server_content:
                    content = response.server_content
                    original += bool(content.input_transcription and content.input_transcription.text)
                    translated += bool(content.output_transcription and content.output_transcription.text)

        receiver = asyncio.create_task(receive())
        source = FileAudioSource(ROOT / "samples/nerdearla-freedos-60s.wav")
        chunks = source.chunks()
        try:
            for _ in range(150):
                chunk = await anext(chunks)
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
            await asyncio.sleep(3)
            if receiver.done():
                receiver.result()
        finally:
            await chunks.aclose()
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
    print(
        f"connection resumed={bool(handle)} updates={updates} "
        f"handle_available={bool(latest_handle)} go_away={go_away} "
        f"english_fragments={original} spanish_fragments={translated}",
        flush=True,
    )
    return latest_handle


async def main() -> None:
    key = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is missing from .env")
    client = genai.Client(api_key=key)
    handle = None
    try:
        handle = await probe_connection(client, None)
        if handle:
            await probe_connection(client, handle)
    except Exception as exc:
        detail = str(exc).replace(key, "[REDACTED]")
        if handle:
            detail = detail.replace(handle, "[REDACTED_HANDLE]")
        raise SystemExit(f"Capability probe failed: {detail}") from None
    finally:
        await client.aio.aclose()


if __name__ == "__main__":
    asyncio.run(main())
