"""Bounded audio buffering and observable provider health without Gemini."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from stagepulse.providers import (
    MAX_RECONNECT_AUDIO_BYTES,
    GeminiLiveTranslateProvider,
    _BufferedAudio,
)


class BufferedAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_newest_frames_without_replaying_consumed_audio(self) -> None:
        buffer = _BufferedAudio()
        frame = bytes(3200)
        await buffer.put(b"a" * 3200)
        self.assertEqual(await buffer.get(), b"a" * 3200)
        for index in range(MAX_RECONNECT_AUDIO_BYTES // len(frame) + 1):
            await buffer.put(bytes([index]) * len(frame))
        await buffer.close()
        self.assertEqual(buffer.dropped_bytes, len(frame))
        received = [chunk async for chunk in self._drain(buffer)]
        self.assertEqual(len(received), MAX_RECONNECT_AUDIO_BYTES // len(frame))
        self.assertEqual(received[0], bytes([1]) * len(frame))
        self.assertIsNone(await buffer.get())

    async def _drain(self, buffer):
        while (chunk := await buffer.get()) is not None:
            yield chunk


class TranslateConfigurationTests(unittest.TestCase):
    def test_resumption_and_sliding_window_are_configured(self) -> None:
        provider = GeminiLiveTranslateProvider("placeholder", "en", "es")
        config = provider._config()
        self.assertIsNone(config.session_resumption.handle)
        self.assertIsNotNone(config.context_window_compression.sliding_window)
        self.assertIsNone(provider.debug_reconnect_after)
        provider._resume_handle = "test-handle"
        self.assertEqual(provider._config().session_resumption.handle, "test-handle")


if __name__ == "__main__":
    unittest.main()
