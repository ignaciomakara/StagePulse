"""Manual Chrome CDP check of stage and audience browser views.

Start the StagePulse server and Chrome with remote debugging first. This script
does not run in the unit test suite and does not substitute for an operator test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import threading
import wave
from pathlib import Path

import httpx
import websockets


ROOT = Path(__file__).resolve().parents[1]


class Page:
    def __init__(self, target: dict) -> None:
        self.target = target
        self.socket = None
        self.next_id = 0

    async def __aenter__(self):
        self.socket = await websockets.connect(self.target["webSocketDebuggerUrl"])
        return self

    async def __aexit__(self, *_):
        await self.socket.close()

    async def evaluate(self, expression: str):
        self.next_id += 1
        command_id = self.next_id
        await self.socket.send(
            json.dumps(
                {
                    "id": command_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            response = json.loads(await asyncio.wait_for(self.socket.recv(), timeout=20))
            if response.get("id") == command_id:
                if "exceptionDetails" in response.get("result", {}):
                    raise RuntimeError(response["result"]["exceptionDetails"]["text"])
                return response.get("result", {}).get("result", {}).get("value")


def targets(port: int) -> list[dict]:
    return httpx.get(f"http://127.0.0.1:{port}/json", timeout=5).json()


def new_tab(port: int, url: str) -> dict:
    return httpx.put(f"http://127.0.0.1:{port}/json/new?{url}", timeout=5).json()


def play_via_output(device: int, stop: threading.Event) -> None:
    import sounddevice as sd

    with wave.open(str(ROOT / "samples/nerdearla-freedos-60s.wav"), "rb") as sample:
        def write_audio(outdata, frames, time_info, status) -> None:
            data = sample.readframes(frames)
            outdata[:] = data.ljust(len(outdata), b"\0")

        with sd.RawOutputStream(
            device=device,
            samplerate=sample.getframerate(),
            channels=sample.getnchannels(),
            dtype="int16",
            callback=write_audio,
        ):
            stop.wait(65)


async def main(
    port: int,
    stage_id: str,
    device_name: str,
    playback: bool,
    output_device: int | None,
    duration: int,
    reload_at: int | None,
) -> None:
    console_target = next(
        item for item in targets(port) if item.get("url") == "http://127.0.0.1:8000/stage"
    )
    async with Page(console_target) as console:
        await console.evaluate("document.querySelector('#devices').click()")
        await asyncio.sleep(3)
        devices = await console.evaluate(
            "Array.from(document.querySelector('#device').options).map(o => ({label:o.textContent,value:o.value}))"
        )
        print("Audio inputs:", [item["label"] for item in devices], flush=True)
        selected = next(
            (item for item in devices if device_name.casefold() in item["label"].casefold()),
            None,
        )
        if selected is None:
            print(f"Audio input containing {device_name!r} is unavailable")
            print("Console status:", await console.evaluate("document.querySelector('#connection').textContent"))
            return
        initial_connections = httpx.get(
            f"http://127.0.0.1:8000/api/stages/{stage_id}", timeout=5
        ).json()["connections"]
        await console.evaluate(
            f"document.querySelector('#stage').value={json.dumps(stage_id)};"
            "document.querySelector('#stage').dispatchEvent(new Event('change'));"
            f"document.querySelector('#device').value={json.dumps(selected['value'])};"
            "document.querySelector('#start').click()"
        )
        await asyncio.sleep(5)
        print("Console connection:", await console.evaluate("document.querySelector('#connection').textContent"), flush=True)
        print("Stage status:", await console.evaluate("document.querySelector('#stage-status').textContent"), flush=True)
        if not await console.evaluate("document.querySelector('#stop').disabled === false"):
            return

        english_target = new_tab(port, f"http://127.0.0.1:8000/audience/{stage_id}")
        spanish_target = new_tab(port, f"http://127.0.0.1:8000/audience/{stage_id}")
        async with Page(english_target) as english, Page(spanish_target) as spanish:
            await asyncio.sleep(2)
            await spanish.evaluate("document.querySelector('#language').value='es'; document.querySelector('#language').dispatchEvent(new Event('change'))")
            player = None
            output_stop = threading.Event()
            output_task = None
            if playback:
                if output_device is not None:
                    output_task = asyncio.create_task(
                        asyncio.to_thread(play_via_output, output_device, output_stop)
                    )
                else:
                    ffplay = shutil.which("ffplay")
                    if not ffplay:
                        raise RuntimeError("ffplay is required to play the sample")
                    player = subprocess.Popen(
                        [ffplay, "-nodisp", "-autoexit", "-loglevel", "error", str(ROOT / "samples/nerdearla-freedos-60s.wav")],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            try:
                checkpoints = sorted(set([point for point in (10, 20, 30, 45, 65) if point <= duration] + [duration]))
                previous = 0
                for second in checkpoints:
                    await asyncio.sleep(second - previous)
                    previous = second
                    if reload_at == second:
                        await console.evaluate("location.reload()")
                        await asyncio.sleep(3)
                        print("Reloaded Stage Console", flush=True)
                    state = httpx.get(f"http://127.0.0.1:8000/api/stages/{stage_id}", timeout=5).json()
                    original = await console.evaluate("Array.from(document.querySelectorAll('#original p')).map(p=>p.textContent)")
                    translated = await console.evaluate("Array.from(document.querySelectorAll('#translated p')).map(p=>p.textContent)")
                    audience_en = await english.evaluate("Array.from(document.querySelectorAll('#captions p')).map(p=>p.textContent)")
                    audience_es = await spanish.evaluate("Array.from(document.querySelectorAll('#captions p')).map(p=>p.textContent)")
                    print(
                        f"t={second}s state={state['state']} new_connections={state['connections'] - initial_connections} viewers={state['viewers']} "
                        f"en={len(original)} es={len(translated)} audience_en={len(audience_en)} audience_es={len(audience_es)} "
                        f"same_en={original == audience_en} same_es={translated == audience_es}",
                        flush=True,
                    )
                    if original:
                        print("EN:", original[-1], flush=True)
                    if translated:
                        print("ES:", translated[-1], flush=True)
            finally:
                if player is not None and player.poll() is None:
                    player.terminate()
                output_stop.set()
                if output_task is not None:
                    await output_task
                await console.evaluate("document.querySelector('#stop').click()")
                await asyncio.sleep(2)
                print("Final status:", httpx.get(f"http://127.0.0.1:8000/api/stages/{stage_id}", timeout=5).json(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-port", type=int, default=9223)
    parser.add_argument("--stage", default="gran-sala")
    parser.add_argument("--device-contains", default="Mezcla estéreo")
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument("--output-device", type=int)
    parser.add_argument("--duration", type=int, default=65)
    parser.add_argument("--reload-at", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.cdp_port, args.stage, args.device_contains, not args.no_playback, args.output_device, args.duration, args.reload_at))
