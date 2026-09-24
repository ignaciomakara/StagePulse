"""PCM audio sources for stage workers."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


SAMPLE_RATE = 16_000
CHUNK_BYTES = 3_200  # 100 ms of mono 16-bit PCM.


class AudioSource(Protocol):
    def chunks(self) -> AsyncIterator[bytes]:
        """Yield raw mono 16-bit PCM at 16 kHz."""


class FileAudioSource:
    """Read an audio or video file through FFmpeg at playback speed."""

    def __init__(self, path: Path) -> None:
        self.path = path

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
                yield chunk
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
