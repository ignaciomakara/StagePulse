"""One real Chrome/Stereo Mix benchmark using the fixed 60-second WAV."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack

import httpx

from manual_gate4_browser import Page, new_tab, play_via_output, targets


PORT = 9235
BASE = "http://127.0.0.1:8000"


async def caption_text(page: Page, selector: str) -> str:
    return await page.evaluate(f"document.querySelector({json.dumps(selector)}).textContent")


async def main() -> None:
    console_target = next(
        item for item in targets(PORT) if item.get("url") == f"{BASE}/stage"
    )
    async with AsyncExitStack() as stack:
        console = await stack.enter_async_context(Page(console_target))
        await console.evaluate("document.querySelector('#devices').click()")
        await asyncio.sleep(3)
        devices = await console.evaluate(
            "Array.from(document.querySelector('#device').options).map(o => ({label:o.textContent,value:o.value}))"
        )
        selected = next(item for item in devices if "Mezcla" in item["label"])
        audience = await stack.enter_async_context(
            Page(new_tab(PORT, f"{BASE}/audience/gran-sala"))
        )
        await asyncio.sleep(2)
        await audience.evaluate(
            "document.querySelector('#language').value='es';"
            "document.querySelector('#language').dispatchEvent(new Event('change'))"
        )
        print("WAITING " + json.dumps({
            "text": await caption_text(audience, "#captions")
        }, ensure_ascii=False), flush=True)
        await console.evaluate(
            "document.querySelector('#stage').value='gran-sala';"
            "document.querySelector('#stage').dispatchEvent(new Event('change'));"
            f"document.querySelector('#device').value={json.dumps(selected['value'])};"
            "document.querySelector('#start').click()"
        )
        for _ in range(60):
            state = httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()
            if state["audio_receiving"]:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Browser did not send PCM to the backend")
        stop_audio = threading.Event()
        playback = asyncio.create_task(asyncio.to_thread(play_via_output, 20, stop_audio))
        late = None
        try:
            for second in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 62):
                await asyncio.sleep(second - (0 if second == 5 else previous))
                previous = second
                if second == 30:
                    late = await stack.enter_async_context(
                        Page(new_tab(PORT, f"{BASE}/audience/gran-sala"))
                    )
                    await asyncio.sleep(2)
                    await late.evaluate(
                        "document.querySelector('#language').value='es';"
                        "document.querySelector('#language').dispatchEvent(new Event('change'))"
                    )
                    print("LATE_INITIAL " + json.dumps({
                        "text": await caption_text(late, "#captions"),
                        "connections": httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()["connections"],
                    }, ensure_ascii=False), flush=True)
                state = httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()
                trace = {
                    "t": second,
                    "state": state["state"],
                    "provider_status": state["provider_status"],
                    "connections": state["connection_count"],
                    "console_en": await caption_text(console, "#original"),
                    "console_es": await caption_text(console, "#translated"),
                    "audience_es": await caption_text(audience, "#captions"),
                    "late_es": await caption_text(late, "#captions") if late else None,
                }
                print("FRONTEND " + json.dumps(trace, ensure_ascii=False), flush=True)
        finally:
            stop_audio.set()
            await playback
            await console.evaluate("document.querySelector('#stop').click()")
            await asyncio.sleep(2)
            state = httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()
            print(f"RESULT state={state['state']} error={state['error']!r} "
                  f"connections={state['connections']} reconnects={state['reconnect_count']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
