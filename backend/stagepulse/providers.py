"""Gemini Live caption providers with one session per stage worker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai
from google.genai import types

from .audio import AudioSource, CHUNK_BYTES, SAMPLE_RATE


TRANSLATE_MODEL = "gemini-3.5-live-translate-preview"
TRANSCRIBE_MODEL = "gemini-3.5-transcribe-live"


@dataclass(frozen=True)
class ProviderTranscript:
    language: str
    text: str
    kind: str  # fragment, interim, final, or boundary


class CaptionProvider(Protocol):
    name: str

    async def run(
        self,
        audio: AudioSource,
        on_transcript: Callable[[ProviderTranscript], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Run one Gemini session until the source ends or the task is cancelled."""


class _GeminiLiveProvider:
    name: str

    def __init__(self, api_key: str, source_language: str) -> None:
        self._api_key = api_key
        self.source_language = source_language

    def _config(self) -> types.LiveConnectConfig:
        raise NotImplementedError

    def _handle(self, content: Any, emit: Callable[[ProviderTranscript], None]) -> None:
        raise NotImplementedError

    async def run(
        self,
        audio: AudioSource,
        on_transcript: Callable[[ProviderTranscript], None],
        on_connected: Callable[[], None],
    ) -> None:
        client = genai.Client(api_key=self._api_key)
        sender: asyncio.Task | None = None
        receiver: asyncio.Task | None = None
        try:
            async with client.aio.live.connect(model=self.name, config=self._config()) as session:
                on_connected()

                async def send_audio() -> None:
                    sent_audio = False
                    async for chunk in audio.chunks():
                        if not chunk:
                            continue
                        sent_audio = True
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=chunk,
                                mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                            )
                        )
                    if not sent_audio:
                        raise RuntimeError("Audio source produced no PCM data")
                    # Give VAD the same short end pause used by Gate 1.
                    for _ in range(15):
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=bytes(CHUNK_BYTES),
                                mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                            )
                        )
                    await session.send_realtime_input(audio_stream_end=True)

                async def receive() -> None:
                    async for response in session.receive():
                        if response.server_content:
                            self._handle(response.server_content, on_transcript)

                sender = asyncio.create_task(send_audio())
                receiver = asyncio.create_task(receive())
                done, _ = await asyncio.wait(
                    {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                if receiver in done and sender not in done:
                    receiver.result()
                    raise RuntimeError("Gemini Live closed before the audio source finished")
                sender.result()
                try:
                    await asyncio.wait_for(receiver, timeout=10)
                except TimeoutError:
                    pass  # The Live session can remain open after stream end.
                if receiver.done():
                    try:
                        receiver.result()
                    except asyncio.CancelledError:
                        pass  # wait_for cancels the receiver at the drain deadline.
        finally:
            tasks = [task for task in (sender, receiver) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Always collect both task outcomes; the SDK can raise while closing.
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await client.aio.aclose()


class GeminiLiveTranslateProvider(_GeminiLiveProvider):
    name = TRANSLATE_MODEL

    def __init__(self, api_key: str, source_language: str, target_language: str) -> None:
        super().__init__(api_key, source_language)
        self.target_language = target_language

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_language,
                echo_target_language=False,
            ),
        )

    def _handle(self, content: Any, emit: Callable[[ProviderTranscript], None]) -> None:
        original = content.input_transcription
        translated = content.output_transcription
        if original and original.text:
            emit(ProviderTranscript(self.source_language, original.text, "fragment"))
        if translated and translated.text:
            emit(ProviderTranscript(self.target_language, translated.text, "fragment"))
        if content.turn_complete:
            emit(ProviderTranscript(self.source_language, "", "boundary"))
            emit(ProviderTranscript(self.target_language, "", "boundary"))


class GeminiTranscribeProvider(_GeminiLiveProvider):
    name = TRANSCRIBE_MODEL

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=[]
            ),
        )

    def _handle(self, content: Any, emit: Callable[[ProviderTranscript], None]) -> None:
        interim = content.interim_input_transcription
        final = content.input_transcription
        if interim and interim.text:
            emit(ProviderTranscript(self.source_language, interim.text, "interim"))
        if final and final.text:
            emit(ProviderTranscript(self.source_language, final.text, "final"))
