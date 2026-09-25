"""Gemini Live caption providers with one session per stage worker."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from google import genai
from google.genai import types

from .audio import AudioSource, CHUNK_BYTES, SAMPLE_RATE
from .diagnostics import StageDiagnostics


TRANSLATE_MODEL = "gemini-3.5-live-translate-preview"
TRANSCRIBE_MODEL = "gemini-3.5-transcribe-live"
MAX_RECONNECT_AUDIO_BYTES = CHUNK_BYTES * 32  # 3.2 seconds at 16 kHz mono PCM.


class _BufferedAudio:
    """Keep only the newest 3.2 seconds while a provider connection is unavailable."""

    def __init__(self) -> None:
        self._chunks: deque[tuple[bytes, float]] = deque()
        self._bytes = 0
        self._closed = False
        self._condition = asyncio.Condition()
        self.dropped_bytes = 0
        self.last_wait_seconds = 0.0
        self.last_queue_depth = 0

    async def put(self, chunk: bytes) -> None:
        async with self._condition:
            while self._chunks and self._bytes + len(chunk) > MAX_RECONNECT_AUDIO_BYTES:
                removed = self._chunks.popleft()
                self._bytes -= len(removed[0])
                self.dropped_bytes += len(removed[0])
            if len(chunk) > MAX_RECONNECT_AUDIO_BYTES:
                self.dropped_bytes += len(chunk)
            else:
                self._chunks.append((chunk, time.monotonic()))
                self._bytes += len(chunk)
                self._condition.notify()

    async def get(self) -> bytes | None:
        async with self._condition:
            while not self._chunks and not self._closed:
                await self._condition.wait()
            if self._chunks:
                chunk, queued_at = self._chunks.popleft()
                self._bytes -= len(chunk)
                self.last_wait_seconds = time.monotonic() - queued_at
                self.last_queue_depth = len(self._chunks)
                return chunk
            return None

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()


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

    def __init__(
        self,
        api_key: str,
        source_language: str,
        target_language: str,
        debug_reconnect_after: float | None = None,
        diagnostics: StageDiagnostics | None = None,
    ) -> None:
        super().__init__(api_key, source_language)
        self.target_language = target_language
        self.debug_reconnect_after = debug_reconnect_after
        self.diagnostics = diagnostics
        self._reconnect_requested = asyncio.Event()
        self.provider_status = "idle"
        self.reconnect_count = 0
        self.last_error: str | None = None
        self.last_provider_event: str | None = None
        self.last_provider_event_at: datetime | None = None
        self.last_audio_at: datetime | None = None
        self.connection_started_at: datetime | None = None
        self.session_started_at: datetime | None = None
        self.go_away_count = 0
        self.go_away_at: datetime | None = None
        self.go_away_time_left: str | None = None
        self.resumption_update_count = 0
        self.forced_reconnect_count = 0
        self.dropped_audio_bytes = 0
        self._resume_handle: str | None = None
        self._connections_this_run = 0
        self._sent_audio = False
        self._sent_pcm_bytes = 0
        self.translation_stall_reconnect_count = 0

    @property
    def latest_resumption_handle_available(self) -> bool:
        return self._resume_handle is not None

    def request_translation_stall_reconnect(self) -> bool:
        """Request one sequential connection rotation from the current session."""
        if self.provider_status != "connected" or self._reconnect_requested.is_set():
            return False
        self._reconnect_requested.set()
        return True

    def _event(self, name: str) -> None:
        self.last_provider_event = name
        self.last_provider_event_at = datetime.now(timezone.utc)

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_language,
                echo_target_language=False,
            ),
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            ),
        )

    async def run(
        self,
        audio: AudioSource,
        on_transcript: Callable[[ProviderTranscript], None],
        on_connected: Callable[[], None],
    ) -> None:
        self._resume_handle = None
        self._connections_this_run = 0
        self._sent_audio = False
        self._sent_pcm_bytes = 0
        self.translation_stall_reconnect_count = 0
        self._reconnect_requested.clear()
        self.reconnect_count = 0
        self.last_error = None
        self.last_audio_at = None
        self.connection_started_at = None
        self.session_started_at = datetime.now(timezone.utc)
        self.go_away_count = 0
        self.go_away_at = None
        self.go_away_time_left = None
        self.resumption_update_count = 0
        self.forced_reconnect_count = 0
        self.dropped_audio_bytes = 0
        self.provider_status = "connecting"
        self._event("starting")

        buffer = _BufferedAudio()

        async def pump_audio() -> None:
            try:
                async for chunk in audio.chunks():
                    if chunk:
                        self.last_audio_at = datetime.now(timezone.utc)
                        await buffer.put(chunk)
                        self.dropped_audio_bytes = buffer.dropped_bytes
            finally:
                await buffer.close()

        client = genai.Client(api_key=self._api_key)
        pump = asyncio.create_task(pump_audio())
        failures = 0
        resume_failures = 0
        recent_attempts: deque[float] = deque()
        try:
            while True:
                now = time.monotonic()
                recent_attempts.append(now)
                while recent_attempts and now - recent_attempts[0] > 30:
                    recent_attempts.popleft()
                if len(recent_attempts) > 6:
                    raise RuntimeError("Gemini Live reconnect limit reached (6 attempts in 30 seconds)")
                self.provider_status = (
                    "connecting" if self._connections_this_run == 0 else "reconnecting"
                )
                try:
                    reason = await self._run_connection(
                        client, buffer, on_transcript, on_connected
                    )
                    failures = 0
                    resume_failures = 0
                    if reason == "complete":
                        await pump
                        self.provider_status = "completed"
                        self._event("completed")
                        return
                    if reason == "forced":
                        self.forced_reconnect_count += 1
                        self._event("forced_reconnect")
                    if reason == "translation_stall":
                        self.translation_stall_reconnect_count += 1
                        self.provider_status = "reconnecting"
                        self._event("translation_stall_reconnect")
                    # GoAway already recorded in the receiver before rotation.
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if pump.done():
                        pump.result()  # Preserve actionable audio-source failures.
                        if not self._sent_audio:
                            raise
                    failures += 1
                    attempted_handle = self._resume_handle
                    if self._resume_handle is not None:
                        resume_failures += 1
                        if resume_failures >= 2:
                            self._resume_handle = None
                    detail = str(exc).replace(self._api_key, "[REDACTED]").strip()
                    if attempted_handle:
                        detail = detail.replace(attempted_handle, "[REDACTED_HANDLE]")
                    self.last_error = detail or exc.__class__.__name__
                    self.provider_status = "reconnecting"
                    self._event("connection_error")
                    if failures >= 5:
                        raise RuntimeError(
                            f"Gemini Live recovery failed after {failures} attempts: {self.last_error}"
                        ) from None
                    await asyncio.sleep((0.5, 1, 2, 4, 8)[failures - 1])
        except asyncio.CancelledError:
            self.provider_status = "stopped"
            raise
        except Exception:
            self.provider_status = "failed"
            raise
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await client.aio.aclose()

    async def _run_connection(
        self,
        client: Any,
        buffer: _BufferedAudio,
        on_transcript: Callable[[ProviderTranscript], None],
        on_connected: Callable[[], None],
    ) -> str:
        sender: asyncio.Task | None = None
        receiver: asyncio.Task | None = None
        force: asyncio.Task | None = None
        recovery: asyncio.Task | None = None
        async with client.aio.live.connect(model=self.name, config=self._config()) as session:
            self._connections_this_run += 1
            if self._connections_this_run > 1:
                self.reconnect_count += 1
            self.connection_started_at = datetime.now(timezone.utc)
            self.provider_status = "connected"
            self._event("connected")
            on_connected()

            async def send_audio() -> None:
                while (chunk := await buffer.get()) is not None:
                    # A frame is removed only once. If send fails mid-frame, it is
                    # not replayed because delivery to Gemini is uncertain.
                    send_started = time.monotonic()
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=chunk, mime_type=f"audio/pcm;rate={SAMPLE_RATE}"
                        )
                    )
                    self._sent_audio = True
                    self._sent_pcm_bytes += len(chunk)
                    if self.diagnostics is not None:
                        self.diagnostics.record(
                            "provider_send", bytes=len(chunk),
                            started_at=send_started,
                            cumulative_bytes=self._sent_pcm_bytes,
                            dropped_bytes=buffer.dropped_bytes,
                            buffer_wait_s=buffer.last_wait_seconds,
                            buffer_depth=buffer.last_queue_depth,
                            send_duration_s=time.monotonic() - send_started,
                        )
                if not self._sent_audio:
                    raise RuntimeError("Audio source produced no PCM data")
                for _ in range(15):
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=bytes(CHUNK_BYTES),
                            mime_type=f"audio/pcm;rate={SAMPLE_RATE}",
                        )
                    )
                await session.send_realtime_input(audio_stream_end=True)

            async def receive() -> str:
                async for response in session.receive():
                    if response.server_content:
                        self._handle(response.server_content, on_transcript)
                    update = response.session_resumption_update
                    if update:
                        self.resumption_update_count += 1
                        if update.resumable and update.new_handle:
                            self._resume_handle = update.new_handle
                        self._event("session_resumption_update")
                    if response.go_away:
                        self.go_away_count += 1
                        self.go_away_at = datetime.now(timezone.utc)
                        self.go_away_time_left = response.go_away.time_left
                        self._event("go_away")
                        return "go_away"
                return "closed"

            sender = asyncio.create_task(send_audio())
            receiver = asyncio.create_task(receive())
            if self.debug_reconnect_after and self.forced_reconnect_count == 0:
                force = asyncio.create_task(asyncio.sleep(self.debug_reconnect_after))
            recovery = asyncio.create_task(self._reconnect_requested.wait())
            tasks = {sender, receiver}
            if force is not None:
                tasks.add(force)
            tasks.add(recovery)
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if sender in done:
                    sender.result()
                    try:
                        await asyncio.wait_for(receiver, timeout=10)
                    except TimeoutError:
                        pass
                    if receiver.done() and not receiver.cancelled():
                        receiver.result()
                    return "complete"
                if receiver in done:
                    reason = receiver.result()
                    if reason == "go_away":
                        return reason
                    raise RuntimeError("Gemini Live closed before the audio source finished")
                if recovery in done:
                    self._reconnect_requested.clear()
                    return "translation_stall"
                return "forced"
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

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
