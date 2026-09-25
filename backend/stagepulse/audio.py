"""PCM audio sources for stage workers."""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Callable, Protocol


SAMPLE_RATE = 16_000
CHUNK_BYTES = 3_200  # 100 ms of mono 16-bit PCM.


class AudioSource(Protocol):
    def chunks(self) -> AsyncIterator[bytes]:
        """Yield raw mono 16-bit PCM at 16 kHz."""


class BrowserAudioSource:
    """Receive browser PCM frames for one stage worker across socket reconnects."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[bytes, float] | None] = asyncio.Queue(maxsize=64)
        self._closed = False
        self._buffered_bytes = 0
        self.on_chunk: Callable[[bytes, int], None] | None = None
        self.on_delivery: Callable[[bytes, float, int], None] | None = None
        self.on_end: Callable[[], None] | None = None

    def feed(self, pcm: bytes) -> None:
        if self._closed:
            raise RuntimeError("Browser audio source is closed")
        if not pcm or len(pcm) % 2 or len(pcm) > CHUNK_BYTES * 10:
            raise ValueError("Expected nonempty 16-bit PCM frames up to one second")
        if self._buffered_bytes + len(pcm) > CHUNK_BYTES * 32:
            raise RuntimeError("Browser audio buffer is full; check the audio connection")
        try:
            self._queue.put_nowait((pcm, time.monotonic()))
            self._buffered_bytes += len(pcm)
            if self.on_chunk is not None:
                self.on_chunk(pcm, self._queue.qsize())
        except asyncio.QueueFull as exc:
            raise RuntimeError("Browser audio buffer is full; check the audio connection") from exc

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._queue.full():
                removed = self._queue.get_nowait()
                if removed is not None:
                    self._buffered_bytes -= len(removed[0])
            self._queue.put_nowait(None)
            if self.on_end is not None:
                self.on_end()

    async def chunks(self) -> AsyncIterator[bytes]:
        while (item := await self._queue.get()) is not None:
            chunk, received_at = item
            self._buffered_bytes -= len(chunk)
            if self.on_delivery is not None:
                self.on_delivery(chunk, time.monotonic() - received_at, self._queue.qsize())
            yield chunk


class FileAudioSource:
    """Read an audio or video file through FFmpeg at playback speed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.on_chunk: Callable[[bytes, int], None] | None = None
        self.on_delivery: Callable[[bytes, float, int], None] | None = None
        self.on_end: Callable[[], None] | None = None

    async def chunks(self) -> AsyncIterator[bytes]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Audio file not found: {self.path}")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg was not found on PATH")

        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-i",
            str(self.path),
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
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            while chunk := await process.stdout.read(CHUNK_BYTES):
                if self.on_chunk is not None:
                    self.on_chunk(chunk, 0)
                if self.on_delivery is not None:
                    self.on_delivery(chunk, 0.0, 0)
                yield chunk
            if self.on_end is not None:
                self.on_end()
            return_code = await process.wait()
            detail = (await stderr_task).decode(errors="replace").strip()
            if return_code:
                raise RuntimeError(
                    f"FFmpeg failed for {self.path} (exit {return_code}): {detail}"
                )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not stderr_task.done():
                stderr_task.cancel()
