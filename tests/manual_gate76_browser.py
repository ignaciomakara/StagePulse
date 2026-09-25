"""Run one real Chrome/Stereo Mix Gate 7.6 diagnostic with the fixed WAV."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from contextlib import AsyncExitStack

import httpx

from manual_gate4_browser import Page, new_tab, play_via_output, targets


BASE = "http://127.0.0.1:8000"


def status() -> dict:
    return httpx.get(f"{BASE}/api/stages/main", timeout=5).json()


async def text(page: Page, selector: str) -> str:
    return await page.evaluate(f"document.querySelector({json.dumps(selector)}).textContent")


async def main(port: int) -> None:
    target = next(item for item in targets(port) if item.get("url") == f"{BASE}/stage")
    async with AsyncExitStack() as stack:
        console = await stack.enter_async_context(Page(target))
        audience = await stack.enter_async_context(Page(new_tab(port, f"{BASE}/audience/main")))
        control = await stack.enter_async_context(Page(new_tab(port, f"{BASE}/control")))
        await console.evaluate("document.querySelector('#devices').click()")
        await asyncio.sleep(3)
        devices = await console.evaluate(
            "Array.from(document.querySelector('#device').options).map(o => ({label:o.textContent,value:o.value}))"
        )
        selected = next(item for item in devices if "Mezcla" in item["label"])
        await audience.evaluate(
            "document.querySelector('#language').value='es';"
            "document.querySelector('#language').dispatchEvent(new Event('change'))"
        )
        await console.evaluate(
            "document.querySelector('#stage').value='main';"
            "document.querySelector('#stage').dispatchEvent(new Event('change'));"
            f"document.querySelector('#device').value={json.dumps(selected['value'])};"
            "document.querySelector('#start').click()"
        )
        for _ in range(60):
            if status()["audio_receiving"]:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Browser did not send PCM to StagePulse")

        stop_audio = threading.Event()
        playback = asyncio.create_task(asyncio.to_thread(play_via_output, 20, stop_audio))
        started = time.monotonic()
        prior = None
        try:
            for second in range(68):
                await asyncio.sleep(max(0, started + second - time.monotonic()))
                state = status()
                marker = (
                    state["translation_status"], state["translation_stall_count"],
                    state["connection_count"], state["reconnect_count"],
                )
                if marker != prior:
                    prior = marker
                    control_translation = await control.evaluate(
                        "Array.from(document.querySelectorAll('.stage-card dt'))"
                        ".find(el => el.textContent === 'Translation')"
                        "?.nextElementSibling?.textContent ?? ''"
                    )
                    print("HEALTH " + json.dumps({
                        "t": round(time.monotonic() - started, 3),
                        "translation_status": state["translation_status"],
                        "stall_active": state["translation_stall_active"],
                        "stall_count": state["translation_stall_count"],
                        "age_raw_en": state["age_last_raw_en"],
                        "age_raw_es": state["age_last_raw_es"],
                        "connections": state["connection_count"],
                        "reconnects": state["reconnect_count"],
                        "stall_reconnects": state["translation_stall_reconnect_count"],
                        "control_translation": control_translation,
                    }), flush=True)
                if second in (5, 10, 20, 30, 40, 50, 60, 67):
                    control_translation = await control.evaluate(
                        "Array.from(document.querySelectorAll('.stage-card dt'))"
                        ".find(el => el.textContent === 'Translation')"
                        "?.nextElementSibling?.textContent ?? ''"
                    )
                    print("FRONTEND " + json.dumps({
                        "t": second,
                        "console_en": await text(console, "#original"),
                        "console_es": await text(console, "#translated"),
                        "audience_es": await text(audience, "#captions"),
                        "control_translation": control_translation,
                    }, ensure_ascii=False), flush=True)
            before_stop = status()
            print("RESULT_BEFORE_STOP " + json.dumps({
                "state": before_stop["state"],
                "stall_count": before_stop["translation_stall_count"],
                "connections": before_stop["connection_count"],
                "reconnects": before_stop["reconnect_count"],
                "stall_reconnects": before_stop["translation_stall_reconnect_count"],
                "error": before_stop["error"],
            }), flush=True)
        finally:
            stop_audio.set()
            await playback
            await console.evaluate("document.querySelector('#stop').click()")
            await asyncio.sleep(2)
            print("RESULT_AFTER_STOP " + json.dumps({
                "state": status()["state"], "error": status()["error"]
            }), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9236)
    args = parser.parse_args()
    asyncio.run(main(args.cdp_port))
