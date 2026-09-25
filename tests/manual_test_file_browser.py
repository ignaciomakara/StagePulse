"""Exercise the browser test-file path with real Gemini and shared caption views."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from manual_gate4_browser import Page, new_tab


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8019"
CDP_PORT = 9239
SAMPLE = ROOT / "samples/nerdearla-freedos-60s.wav"


async def command(page: Page, method: str, params: dict) -> dict:
    page.next_id += 1
    command_id = page.next_id
    await page.socket.send(json.dumps({"id": command_id, "method": method, "params": params}))
    while True:
        response = json.loads(await asyncio.wait_for(page.socket.recv(), timeout=20))
        if response.get("id") == command_id:
            if "error" in response:
                raise RuntimeError(response["error"])
            return response.get("result", {})


def status() -> dict:
    return httpx.get(f"{BASE}/api/stages/main", timeout=5).json()


async def latest(page: Page, selector: str) -> str:
    return await page.evaluate(
        f"document.querySelector({json.dumps(selector)})?.lastElementChild?.textContent || ''"
    )


async def main() -> None:
    if not SAMPLE.is_file():
        raise FileNotFoundError(SAMPLE)
    async with Page(new_tab(CDP_PORT, BASE + "/stage")) as stage:
        await asyncio.sleep(2)
        assert await stage.evaluate("document.querySelector('h1').textContent") == "Stage Console"
        assert await stage.evaluate("document.querySelector('#source-mode option[value=file]').textContent") == "Test file"
        await stage.evaluate(
            "document.querySelector('#ui-language').value='es';"
            "document.querySelector('#ui-language').dispatchEvent(new Event('change'))"
        )
        assert await stage.evaluate("document.querySelector('h1').textContent") == "Consola de escenario"
        assert await stage.evaluate("document.querySelector('#source-mode option[value=file]').textContent") == "Archivo de prueba"
        assert await stage.evaluate("document.querySelector('nav').getAttribute('aria-label')") == "Navegación del escenario"
        await stage.evaluate(
            "document.querySelector('#source-mode').value='file';"
            "document.querySelector('#source-mode').dispatchEvent(new Event('change'))"
        )
        document = await command(stage, "DOM.getDocument", {})
        node = await command(stage, "DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": "#test-file-input",
        })
        await command(stage, "DOM.setFileInputFiles", {
            "nodeId": node["nodeId"], "files": [str(SAMPLE)],
        })
        await asyncio.sleep(0.3)
        print("file selected:", await stage.evaluate("document.querySelector('#test-file-name').textContent"), flush=True)
        print("live controls hidden:", await stage.evaluate("document.querySelector('#live-input-controls').hidden"), flush=True)
        print("Spanish UI:", await stage.evaluate("document.querySelector('#choose-test-file').textContent"), flush=True)
        previous_connections = status()["connections"]
        await stage.evaluate("document.querySelector('#start').click()")
        for _ in range(15):
            await asyncio.sleep(1)
            if status()["state"] in {"running", "failed"}:
                break
        active = status()
        started_connections = active["connections"]
        print("after start:", {key: active[key] for key in ("state", "source_mode", "connections", "provider_status", "error")}, flush=True)
        if active["state"] == "failed":
            return
        try:
            async with websockets.connect("ws://127.0.0.1:8019/ws/stages/main/audio") as audio:
                await audio.recv()
        except ConnectionClosed as error:
            assert error.code == 1008, error.code
            print("second audio source rejected:", error.code, flush=True)
        else:
            raise AssertionError("A live audio source connected while the test file was active")
        await stage.evaluate("location.reload()")
        await asyncio.sleep(2)
        assert await stage.evaluate("document.querySelector('#source-mode').value") == "file"
        assert await stage.evaluate("document.querySelector('#stop').disabled") is False
        print("Stage Console restored active test file after reload", flush=True)
        async with (
            Page(new_tab(CDP_PORT, BASE + "/audience/main")) as audience_en,
            Page(new_tab(CDP_PORT, BASE + "/audience/main")) as audience_es,
            Page(new_tab(CDP_PORT, BASE + "/overlay/main?lang=original")) as overlay_en,
            Page(new_tab(CDP_PORT, BASE + "/overlay/main?lang=es")) as overlay_es,
            Page(new_tab(CDP_PORT, BASE + "/control")) as control,
        ):
            await asyncio.sleep(1)
            assert await audience_en.evaluate("document.querySelector('#stage-title').textContent") == "Main Stage"
            assert await audience_en.evaluate("document.querySelector('[data-i18n=captionLanguage]').textContent") == "Idioma de los subtítulos"
            assert await control.evaluate("document.querySelector('h1').textContent") == "Sala de control"
            await audience_es.evaluate(
                "document.querySelector('#language').value='es';"
                "document.querySelector('#language').dispatchEvent(new Event('change'))"
            )
            observed = False
            for second in range(1, 41):
                await asyncio.sleep(1)
                current = status()
                console_en = await latest(stage, "#original")
                console_es = await latest(stage, "#translated")
                audience_english = await latest(audience_en, "#captions")
                audience_spanish = await latest(audience_es, "#captions")
                overlay_english = await overlay_en.evaluate("document.querySelector('#overlay-caption').textContent")
                overlay_spanish = await overlay_es.evaluate("document.querySelector('#overlay-caption').textContent")
                if all((console_en, console_es, audience_english, audience_spanish, overlay_english, overlay_spanish)):
                    observed = True
                    print("shared captions at +", second, "s:", flush=True)
                    print("EN:", console_en, flush=True)
                    print("ES:", console_es, flush=True)
                    print("audience EN/ES:", audience_english, "/", audience_spanish, flush=True)
                    print("overlay EN/ES:", overlay_english, "/", overlay_spanish, flush=True)
                    print("connections previous/start/after viewers:", previous_connections, started_connections, current["connections"], "viewers:", current["viewers"], flush=True)
                    await stage.evaluate(
                        "document.querySelector('#ui-language').value='en';"
                        "document.querySelector('#ui-language').dispatchEvent(new Event('change'))"
                    )
                    await asyncio.sleep(0.5)
                    assert await audience_es.evaluate("document.querySelector('#language').value") == "es"
                    assert await audience_es.evaluate("document.querySelector('[data-i18n=captionLanguage]').textContent") == "Caption language"
                    assert await control.evaluate("document.querySelector('h1').textContent") == "Control Room"
                    print("UI switched to English; caption selection stayed Spanish", flush=True)
                    break
                if current["state"] == "failed":
                    print("stage failed:", current["error"], flush=True)
                    break
            print("all views received captions:", observed, flush=True)
            assert observed, "Real captions did not reach every view"
            await command(audience_es, "Emulation.setDeviceMetricsOverride", {
                "width": 1366, "height": 768, "deviceScaleFactor": 1, "mobile": False,
            })
            for _ in range(50):
                english_lines = await audience_en.evaluate(
                    "Array.from(document.querySelectorAll('#captions p')).map(p => p.textContent)"
                )
                spanish_lines = await audience_es.evaluate(
                    "Array.from(document.querySelectorAll('#captions p')).map(p => p.textContent)"
                )
                if len(english_lines) == 3 and len(spanish_lines) == 3:
                    break
                await asyncio.sleep(1)
            assert len(english_lines) == len(spanish_lines) == 3, (
                f"Rolling context incomplete: EN={english_lines}, ES={spanish_lines}"
            )
            print("desktop rolling EN:", english_lines, flush=True)
            print("desktop rolling ES:", spanish_lines, flush=True)
            desktop = await audience_es.evaluate(
                "Array.from(document.querySelectorAll('#captions p')).map(p => ({"
                "className:p.className,display:getComputedStyle(p).display,"
                "fontSize:getComputedStyle(p).fontSize}))"
            )
            assert [line["display"] for line in desktop] == ["block"] * 3, desktop
            assert float(desktop[-1]["fontSize"].removesuffix("px")) > float(
                desktop[0]["fontSize"].removesuffix("px")
            ), desktop
            print("desktop caption styles:", desktop, flush=True)
            await audience_es.evaluate(
                "document.querySelector('#language').value='en';"
                "document.querySelector('#language').dispatchEvent(new Event('change'))"
            )
            assert await audience_es.evaluate("document.querySelector('#captions p:last-child').textContent")
            await audience_es.evaluate(
                "document.querySelector('#language').value='es';"
                "document.querySelector('#language').dispatchEvent(new Event('change'))"
            )
            assert await audience_es.evaluate("document.querySelector('#language').value") == "es"
            print("caption language switched EN -> ES with recent captions retained", flush=True)
            await command(audience_es, "Emulation.setDeviceMetricsOverride", {
                "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
            })
            mobile = await audience_es.evaluate(
                "Array.from(document.querySelectorAll('#captions p')).map(p => ({"
                "className:p.className,display:getComputedStyle(p).display}))"
            )
            assert [line["display"] for line in mobile] == ["none", "block", "block"], mobile
            print("mobile caption visibility:", mobile, flush=True)
            await audience_es.evaluate("location.reload()")
            await asyncio.sleep(2)
            assert await audience_es.evaluate("document.querySelector('#connection').dataset.state") == "connected"
            assert len(await audience_es.evaluate("Array.from(document.querySelectorAll('#captions p')).map(p => p.textContent)")) <= 2
            print("audience refresh reconnected to current snapshot:", await audience_es.evaluate(
                "Array.from(document.querySelectorAll('#captions p')).map(p => p.textContent)"
            ), flush=True)
            await stage.evaluate("document.querySelector('#stop').click()")
            await asyncio.sleep(2)
            final = status()
            print("after stop:", {key: final[key] for key in ("state", "source_mode", "connections", "viewers")}, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
