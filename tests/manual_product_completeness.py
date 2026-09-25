"""Check the product views with real Test File audio and Gemini in Chrome CDP.

Start StagePulse on port 8019, tests/iframe_check.html on port 8020, and Chrome
with remote debugging on port 9239 before running this manual validation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from manual_gate4_browser import Page, new_tab
from manual_test_file_browser import command


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8019"
SAMPLE = ROOT / "samples/nerdearla-freedos-60s.wav"


def stage_status() -> dict:
    return httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()


async def frame_eval(page: Page, expression: str):
    tree = await command(page, "Page.getFrameTree", {})
    frame_id = tree["frameTree"]["childFrames"][0]["frame"]["id"]
    world = await command(page, "Page.createIsolatedWorld", {
        "frameId": frame_id, "worldName": "stagepulse-check",
    })
    result = await command(page, "Runtime.evaluate", {
        "expression": expression,
        "contextId": world["executionContextId"],
        "awaitPromise": True,
        "returnByValue": True,
    })
    if "exceptionDetails" in result:
        raise RuntimeError(result["exceptionDetails"]["text"])
    return result.get("result", {}).get("value")


async def text(page: Page, selector: str) -> str:
    return await page.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent || ''")


async def main() -> None:
    if not SAMPLE.is_file():
        raise FileNotFoundError(SAMPLE)
    iframe_url = "http://127.0.0.1:8020/iframe_check.html?origin=http://127.0.0.1:8019"
    async with (
        Page(new_tab(9239, BASE + "/stage?stage=gran-sala")) as stage,
        Page(new_tab(9239, BASE + "/audience")) as hub,
        Page(new_tab(9239, BASE + "/audience/gran-sala")) as audience,
        Page(new_tab(9239, BASE + "/display/gran-sala?lang=es")) as display_es,
        Page(new_tab(9239, BASE + "/display/gran-sala?lang=both")) as display_both,
        Page(new_tab(9239, BASE + "/overlay/gran-sala?lang=es")) as overlay,
        Page(new_tab(9239, BASE + "/control")) as control,
        Page(new_tab(9239, iframe_url)) as iframe_page,
    ):
        await asyncio.sleep(2)
        assert await hub.evaluate("document.querySelectorAll('.hub-stage').length") == 3
        assert await hub.evaluate("document.querySelector('.hub-stage h2').textContent") == "Gran sala"
        assert await frame_eval(iframe_page, "document.querySelector('h1').textContent") == "Choose your stage"
        await frame_eval(iframe_page, "document.querySelector('.hub-stage[href=\"/audience/gran-sala\"]').click()")
        await asyncio.sleep(1)
        assert await frame_eval(iframe_page, "document.querySelector('#stage-title').textContent") == "Gran sala"
        print("Cross-origin iframe: hub loaded, stage selected", flush=True)

        await stage.evaluate("document.querySelector('#source-mode').value='file';document.querySelector('#source-mode').dispatchEvent(new Event('change'))")
        document = await command(stage, "DOM.getDocument", {})
        file_input = await command(stage, "DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": "#test-file-input",
        })
        await command(stage, "DOM.setFileInputFiles", {
            "nodeId": file_input["nodeId"], "files": [str(SAMPLE)],
        })
        await stage.evaluate("document.querySelector('#start').click()")
        try:
            for _ in range(45):
                await asyncio.sleep(1)
                status = stage_status()
                values = {
                    "console_en": await text(stage, "#original p:last-child"),
                    "console_es": await text(stage, "#translated p:last-child"),
                    "audience": await text(audience, "#captions p:last-child"),
                    "display_es": await text(display_es, "#display-spanish .display-text"),
                    "display_both_en": await text(display_both, "#display-original .display-text"),
                    "display_both_es": await text(display_both, "#display-spanish .display-text"),
                    "overlay_es": await text(overlay, "#overlay-caption"),
                    "iframe": await frame_eval(iframe_page, "document.querySelector('#captions p:last-child')?.textContent || ''"),
                }
                if all(values.values()) and "Waiting" not in values["display_es"]:
                    break
                if status["state"] == "failed":
                    raise RuntimeError(f"Stage failed: {status['error']}")
            else:
                raise AssertionError(f"Missing real captions: {values}")
            assert status["connections"] == 1, status["connections"]
            print("Real captions reached:", list(values), flush=True)
            print("EN:", values["console_en"], flush=True)
            print("ES:", values["console_es"], flush=True)
            print("Gemini connections:", status["connections"], "caption subscribers:", status["viewers"], flush=True)
            assert await hub.evaluate("document.querySelector('.hub-stage .stage-indicator').textContent") == "Live"
            assert await control.evaluate("document.querySelector('.stage-card h2').textContent") == "Gran sala"
            print("Hub Gran sala status: Live; Control Room stage present", flush=True)

            await command(display_both, "Emulation.setDeviceMetricsOverride", {
                "width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False,
            })
            dimensions = await display_both.evaluate("({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,mode:document.querySelector('#display-captions').dataset.mode,original:document.querySelector('#display-original').hidden,spanish:document.querySelector('#display-spanish').hidden})")
            assert dimensions == {"width": 1920, "height": 1080, "scrollWidth": 1920, "mode": "both", "original": False, "spanish": False}, dimensions
            print("Display at 1920x1080:", dimensions, flush=True)
            assert await display_es.evaluate("document.querySelector('#display-original').hidden") is True
            assert await display_es.evaluate("document.querySelector('#display-spanish').hidden") is False

            await hub.evaluate("document.querySelector('#ui-language').value='es';document.querySelector('#ui-language').dispatchEvent(new Event('change'))")
            await asyncio.sleep(0.5)
            assert await hub.evaluate("document.querySelector('h1').textContent") == "Elegí tu escenario"
            assert await hub.evaluate("document.querySelector('.hub-stage h2').textContent") == "Gran sala"
            print("Hub Spanish UI and configured stage name verified", flush=True)

            await display_both.evaluate("location.reload()")
            await asyncio.sleep(2)
            assert await display_both.evaluate("document.querySelector('#display-status').dataset.state") == "connected"
            assert await display_both.evaluate("document.querySelector('#display-original .display-text').classList.contains('waiting')") is False
            assert await display_both.evaluate("document.querySelector('#display-spanish .display-text').classList.contains('waiting')") is False
            assert stage_status()["connections"] == 1
            print("Display refresh: connected, current EN/ES snapshots, still one Gemini connection", flush=True)
        finally:
            await stage.evaluate("document.querySelector('#stop').click()")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
