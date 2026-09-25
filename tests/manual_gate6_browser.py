"""Exercise all production views with one real browser audio stream.

Start backend/serve.py with --debug-reconnect-after 25 and a Chrome CDP
instance on port 9231 before running this script. This is a manual real-audio
check, not part of the unit test suite.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack

import httpx

from manual_gate4_browser import Page, new_tab, play_via_output, targets


CDP_PORT = 9231
BASE = "http://127.0.0.1:8000"


def status() -> dict:
    return httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()


async def main() -> None:
    console_target = next(item for item in targets(CDP_PORT) if item.get("url") == f"{BASE}/stage")
    async with AsyncExitStack() as stack:
        console = await stack.enter_async_context(Page(console_target))
        await console.evaluate("document.querySelector('#devices').click()")
        await asyncio.sleep(3)
        devices = await console.evaluate(
            "Array.from(document.querySelector('#device').options).map(o => ({label:o.textContent,value:o.value}))"
        )
        selected = next(item for item in devices if "Mezcla" in item["label"])
        await console.evaluate(
            "document.querySelector('#stage').value='gran-sala';"
            "document.querySelector('#stage').dispatchEvent(new Event('change'));"
            f"document.querySelector('#device').value={json.dumps(selected['value'])};"
            "document.querySelector('#start').click()"
        )
        await asyncio.sleep(4)
        urls = {
            "audience_1": f"{BASE}/audience/gran-sala",
            "audience_2": f"{BASE}/audience/gran-sala",
            "overlay_original": f"{BASE}/overlay/gran-sala?lang=original",
            "overlay_es": f"{BASE}/overlay/gran-sala?lang=es",
            "control": f"{BASE}/control",
        }
        targets_by_name = {name: new_tab(CDP_PORT, url) for name, url in urls.items()}
        pages = {
            name: await stack.enter_async_context(Page(target))
            for name, target in targets_by_name.items()
        }
        await asyncio.sleep(2)
        await pages["audience_2"].evaluate(
            "document.querySelector('#language').value='es';"
            "document.querySelector('#language').dispatchEvent(new Event('change'))"
        )
        print("QR URL:", await console.evaluate("document.querySelector('#audience-url').value"), flush=True)
        print("QR warning:", await console.evaluate("document.querySelector('#audience-warning').textContent"), flush=True)
        print("Control cards:", await pages["control"].evaluate("document.querySelectorAll('.stage-card').length"), flush=True)
        print("Overlay background:", await pages["overlay_original"].evaluate("getComputedStyle(document.documentElement).backgroundColor"), flush=True)
        stop_audio = threading.Event()
        playback = asyncio.create_task(asyncio.to_thread(play_via_output, 20, stop_audio))
        try:
            elapsed = 0
            for second in (10, 20, 30, 40, 55):
                await asyncio.sleep(second - elapsed)
                elapsed = second
                if second == 40:
                    httpx.get(f"http://127.0.0.1:{CDP_PORT}/json/close/{targets_by_name['audience_2']['id']}", timeout=5)
                    await asyncio.sleep(1)
                if second == 55:
                    replacement = new_tab(CDP_PORT, urls["audience_2"])
                    pages["audience_2"] = await stack.enter_async_context(Page(replacement))
                    await asyncio.sleep(1)
                    await pages["audience_2"].evaluate(
                        "document.querySelector('#language').value='es';"
                        "document.querySelector('#language').dispatchEvent(new Event('change'))"
                    )
                    await asyncio.sleep(2)
                current = status()
                english = await pages["audience_1"].evaluate("document.querySelector('#captions').textContent")
                spanish = await pages["audience_2"].evaluate("document.querySelector('#captions').textContent") if second != 40 else "closed"
                overlay_en = await pages["overlay_original"].evaluate("document.querySelector('#overlay-caption').textContent")
                overlay_es = await pages["overlay_es"].evaluate("document.querySelector('#overlay-caption').textContent")
                control_main = await pages["control"].evaluate("document.querySelector('.stage-card').innerText")
                control_connections = f"Connections\n{current['connection_count']}" in control_main
                control_reconnects = f"Reconnects\n{current['reconnect_count']}" in control_main
                print(
                    f"t={second}s state={current['state']} connections={current['connection_count']} "
                    f"reconnects={current['reconnect_count']} provider={current['provider_status']} "
                    f"subscribers={current['viewers']} en={bool(english)} es={bool(spanish) if second != 40 else 'closed'} "
                    f"overlay_en={bool(overlay_en)} overlay_es={bool(overlay_es)} "
                    f"control_connections={control_connections} "
                    f"control_reconnects={control_reconnects}",
                    flush=True,
                )
                if overlay_en:
                    print("Overlay EN:", overlay_en, flush=True)
                if overlay_es:
                    print("Overlay ES:", overlay_es, flush=True)
        finally:
            stop_audio.set()
            await playback
            await console.evaluate("document.querySelector('#stop').click()")
            await asyncio.sleep(2)
            print("Final status:", status(), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
