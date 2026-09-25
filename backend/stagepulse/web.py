"""Small HTTP and WebSocket bridge for stage audio and shared captions."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .audio import BrowserAudioSource
from .manager import StageManager


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def create_app(manager: StageManager) -> FastAPI:
    app = FastAPI(title="StagePulse")
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    audio_sockets: dict[str, WebSocket] = {}
    disconnect_tasks: dict[str, asyncio.Task] = {}
    locks = {stage_id: asyncio.Lock() for stage_id in manager.workers}

    def status_payload(stage_id: str) -> dict:
        payload = asdict(manager.status(stage_id))
        payload["audio_connected"] = stage_id in audio_sockets
        payload["viewers"] = manager.bus.subscriber_count(stage_id)
        return payload

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/stage")

    @app.get("/stage")
    def stage_console() -> FileResponse:
        return FileResponse(FRONTEND / "stage.html")

    @app.get("/audience/{stage_id}")
    def audience(stage_id: str) -> FileResponse:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return FileResponse(FRONTEND / "audience.html")

    @app.get("/api/stages")
    def stages() -> list[dict]:
        return [status_payload(stage_id) for stage_id in manager.workers]

    @app.get("/api/stages/{stage_id}")
    def status(stage_id: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return status_payload(stage_id)

    @app.post("/api/stages/{stage_id}/stop")
    async def stop(stage_id: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        async with locks[stage_id]:
            pending = disconnect_tasks.pop(stage_id, None)
            if pending is not None:
                pending.cancel()
            await manager.stop(stage_id)
            source = manager.workers[stage_id].audio
            if isinstance(source, BrowserAudioSource):
                source.close()
            socket = audio_sockets.pop(stage_id, None)
            if socket is not None:
                await socket.close()
        return status_payload(stage_id)

    @app.websocket("/ws/stages/{stage_id}/audio")
    async def audio(stage_id: str, websocket: WebSocket) -> None:
        if stage_id not in manager.workers:
            await websocket.close(code=1008, reason="Unknown stage")
            return
        await websocket.accept()
        async with locks[stage_id]:
            if stage_id in audio_sockets:
                await websocket.close(code=1008, reason="Stage already has an audio browser")
                return
            pending = disconnect_tasks.pop(stage_id, None)
            if pending is not None:
                pending.cancel()
            worker = manager.workers[stage_id]
            resumed = worker.status.state in {"starting", "running"}
            if resumed:
                if not isinstance(worker.audio, BrowserAudioSource):
                    await websocket.close(code=1008, reason="Stage uses a file source")
                    return
                source = worker.audio
            else:
                source = BrowserAudioSource()
                worker.audio = source
                manager.start(stage_id)
            audio_sockets[stage_id] = websocket
        try:
            await websocket.send_json({"type": "ready", "resumed": resumed})
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if manager.status(stage_id).state == "failed":
                    await websocket.send_json(
                        {"type": "error", "detail": manager.status(stage_id).error}
                    )
                    await websocket.close(code=1011)
                    break
                pcm = message.get("bytes")
                if pcm is None:
                    await websocket.close(code=1003, reason="Binary PCM frames required")
                    break
                try:
                    source.feed(pcm)
                except (ValueError, RuntimeError) as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    await websocket.close(code=1003)
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            async with locks[stage_id]:
                if audio_sockets.get(stage_id) is websocket:
                    del audio_sockets[stage_id]
                    async def stop_if_not_reconnected() -> None:
                        try:
                            await asyncio.sleep(15)
                            async with locks[stage_id]:
                                if stage_id not in audio_sockets and worker.audio is source:
                                    await manager.stop(stage_id)
                                    source.close()
                        except asyncio.CancelledError:
                            pass

                    disconnect_tasks[stage_id] = asyncio.create_task(
                        stop_if_not_reconnected()
                    )

    @app.websocket("/ws/stages/{stage_id}/captions")
    async def captions(stage_id: str, websocket: WebSocket) -> None:
        if stage_id not in manager.workers:
            await websocket.close(code=1008, reason="Unknown stage")
            return
        await websocket.accept()
        subscription = manager.bus.subscribe(stage_id)
        async def watch_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        async def forward() -> None:
            async for event in subscription:
                await websocket.send_json(
                    {
                        "stage_id": event.stage_id,
                        "language": event.language,
                        "text": event.text,
                        "is_final": event.is_final,
                        "timestamp": event.timestamp.isoformat(),
                        "provider": event.provider,
                    }
                )

        reader = asyncio.create_task(watch_disconnect())
        writer = asyncio.create_task(forward())
        try:
            await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for task in (reader, writer):
                task.cancel()
            await asyncio.gather(reader, writer, return_exceptions=True)
            subscription.close()

    return app
