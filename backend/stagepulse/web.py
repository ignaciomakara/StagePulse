"""Small HTTP and WebSocket bridge for stage audio and shared captions."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import qrcode
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from qrcode.image.svg import SvgPathImage

from .audio import BrowserAudioSource, FileAudioSource
from .manager import StageManager
from .talk_prep import ApplyTerms, TalkMetadata, TalkPrepError


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
MAX_TEST_FILE_BYTES = 100 * 1024 * 1024
TEST_FILE_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".webm"}


def create_app(manager: StageManager, public_base_url: str | None = None) -> FastAPI:
    if public_base_url:
        parsed = urlsplit(public_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("STAGEPULSE_PUBLIC_BASE_URL must be an HTTP(S) origin")
        public_base_url = public_base_url.rstrip("/")
    app = FastAPI(title="StagePulse")
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    audio_sockets: dict[str, WebSocket] = {}
    disconnect_tasks: dict[str, asyncio.Task] = {}
    uploaded_files: dict[str, Path] = {}
    locks = {stage_id: asyncio.Lock() for stage_id in manager.workers}

    def discard_uploaded_file(stage_id: str, path: Path) -> None:
        if uploaded_files.get(stage_id) != path:
            return
        uploaded_files.pop(stage_id)
        path.unlink(missing_ok=True)
        worker = manager.workers[stage_id]
        if isinstance(worker.audio, FileAudioSource) and worker.audio.path == path:
            worker.audio = FileAudioSource(worker.config.audio_file)

    async def discard_when_finished(stage_id: str, path: Path) -> None:
        await manager.workers[stage_id].wait()
        async with locks[stage_id]:
            discard_uploaded_file(stage_id, path)

    def audience_url(request: Request, stage_id: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        origin = public_base_url or str(request.base_url).rstrip("/")
        host = urlsplit(origin).hostname
        return {
            "url": f"{origin}/audience/{quote(stage_id, safe='')}",
            "local_only": host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"},
        }

    def status_payload(stage_id: str) -> dict:
        payload = asdict(manager.status(stage_id))
        payload["audio_connected"] = stage_id in audio_sockets
        payload["browser_connected"] = payload["audio_connected"]
        payload["viewers"] = manager.bus.subscriber_count(stage_id)
        payload["connection_count"] = payload["connections"]
        payload["provider_connected"] = payload["provider_status"] == "connected"
        payload["source_mode"] = "test_file" if stage_id in uploaded_files else (
            "live_input" if isinstance(manager.workers[stage_id].audio, BrowserAudioSource) else None
        )
        now = datetime.now(timezone.utc)
        last_audio = payload["last_audio_at"]
        payload["audio_receiving"] = bool(
            last_audio
            and payload["state"] in {"starting", "running"}
            and (now - last_audio).total_seconds() < 2
        )
        session_start = payload["session_started_at"]
        connection_start = payload["connection_started_at"]
        payload["session_age_seconds"] = (
            round((now - session_start).total_seconds(), 1)
            if session_start and payload["state"] in {"starting", "running"}
            else None
        )
        payload["connection_age_seconds"] = (
            round((now - connection_start).total_seconds(), 1)
            if connection_start and payload["provider_status"] == "connected"
            else None
        )
        return payload

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/stage")

    @app.get("/stage")
    def stage_console() -> FileResponse:
        return FileResponse(FRONTEND / "stage.html")

    @app.get("/audience")
    def audience_hub() -> FileResponse:
        return FileResponse(FRONTEND / "audience-hub.html")

    @app.get("/audience/{stage_id}")
    def audience(stage_id: str) -> FileResponse:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return FileResponse(FRONTEND / "audience.html")

    @app.get("/display/{stage_id}")
    def display(stage_id: str, lang: Literal["original", "es", "both"] = "both") -> FileResponse:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return FileResponse(FRONTEND / "display.html")

    @app.get("/control")
    def control_room() -> FileResponse:
        return FileResponse(FRONTEND / "control.html")

    @app.get("/overlay/{stage_id}")
    def overlay(stage_id: str, lang: Literal["original", "es"] = "original") -> FileResponse:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return FileResponse(FRONTEND / "overlay.html")

    @app.get("/api/stages/{stage_id}/audience-link")
    def audience_link(stage_id: str, request: Request) -> dict:
        return audience_url(request, stage_id)

    @app.get("/api/stages/{stage_id}/audience-qr.svg")
    def audience_qr(stage_id: str, request: Request) -> Response:
        url = audience_url(request, stage_id)["url"]
        qr = qrcode.make(url, image_factory=SvgPathImage, border=4)
        output = BytesIO()
        qr.save(output)
        return Response(
            output.getvalue(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/stages")
    def stages() -> list[dict]:
        return [status_payload(stage_id) for stage_id in manager.workers]

    @app.get("/api/stages/{stage_id}")
    def status(stage_id: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return status_payload(stage_id)

    @app.get("/api/stages/{stage_id}/talk-prep")
    def talk_prep_state(stage_id: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        return manager.talk_prep_state(stage_id)

    @app.post("/api/stages/{stage_id}/talk-prep/suggestions")
    async def suggest_talk_terms(stage_id: str, metadata: TalkMetadata) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        if manager.status(stage_id).state in {"starting", "running"}:
            raise HTTPException(409, "Stop the stage before preparing terminology")
        try:
            terms = await asyncio.to_thread(manager.talk_prep.suggest, metadata)
        except TalkPrepError as error:
            raise HTTPException(502, str(error)) from None
        return {"terms": [term.model_dump() for term in terms]}

    @app.post("/api/stages/{stage_id}/talk-prep")
    async def apply_talk_terms(stage_id: str, payload: ApplyTerms) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        async with locks[stage_id]:
            try:
                return manager.apply_talk_terms(stage_id, payload.terms)
            except RuntimeError as error:
                raise HTTPException(409, str(error)) from None
            except ValueError as error:
                raise HTTPException(422, str(error)) from None

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
            uploaded = uploaded_files.get(stage_id)
            if uploaded is not None:
                discard_uploaded_file(stage_id, uploaded)
            socket = audio_sockets.pop(stage_id, None)
            if socket is not None:
                await socket.close()
        return status_payload(stage_id)

    @app.post("/api/stages/{stage_id}/test-file")
    async def start_test_file(stage_id: str, request: Request, filename: str) -> dict:
        if stage_id not in manager.workers:
            raise HTTPException(404, "Unknown stage")
        suffix = Path(filename).suffix.lower()
        if suffix not in TEST_FILE_SUFFIXES:
            raise HTTPException(415, "Unsupported audio or video file extension")
        async with locks[stage_id]:
            worker = manager.workers[stage_id]
            if stage_id in audio_sockets or worker.status.state in {"starting", "running"}:
                raise HTTPException(409, "Stage already has an active audio source")
            previous = uploaded_files.get(stage_id)
            if previous is not None:
                discard_uploaded_file(stage_id, previous)
            fd, name = tempfile.mkstemp(prefix="stagepulse-test-", suffix=suffix)
            path = Path(name)
            size = 0
            try:
                with os.fdopen(fd, "wb") as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_TEST_FILE_BYTES:
                            raise HTTPException(413, "Test file exceeds the 100 MB limit")
                        output.write(chunk)
                if not size:
                    raise HTTPException(400, "Test file is empty")
                worker.audio = FileAudioSource(path)
                manager.start(stage_id)
                uploaded_files[stage_id] = path
                asyncio.create_task(discard_when_finished(stage_id, path))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
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
