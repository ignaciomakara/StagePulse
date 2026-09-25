"""Validate Smart Talk Prep and a full real Test File run in Chrome.

Start StagePulse on port 8019 and Chrome CDP on port 9239 first.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import websockets

from manual_gate4_browser import Page, new_tab
from manual_test_file_browser import command


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8019"
SAMPLE = ROOT / "samples/nerdearla-freedos-60s.wav"


def status() -> dict:
    return httpx.get(f"{BASE}/api/stages/gran-sala", timeout=5).json()


async def latest(page: Page, selector: str) -> str:
    return await page.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent || ''")


async def main() -> None:
    if not SAMPLE.is_file():
        raise FileNotFoundError(SAMPLE)
    async with Page(new_tab(9239, BASE + "/stage?stage=gran-sala")) as stage:
        await asyncio.sleep(2)
        await stage.evaluate(
            "document.querySelector('#talk-title').value='30 years of open source with the FreeDOS Project';"
            "document.querySelector('#talk-speaker').value='Jim Hall';"
            "document.querySelector('#talk-abstract').value='A retrospective on the FreeDOS Project and open source. Jim Hall discusses DOS applications, memory constraints, text and console mode, and WordPerfect.';"
            "document.querySelector('#suggest-terms').click()"
        )
        for _ in range(55):
            await asyncio.sleep(1)
            preparation = await latest(stage, "#talk-prep-status")
            if "Review and apply" in preparation or "No new suggestions" in preparation:
                break
            if "failed" in preparation.lower():
                raise RuntimeError(preparation)
        else:
            raise TimeoutError(f"No suggestion result: {preparation}")
        suggestions = await stage.evaluate(
            "Array.from(document.querySelectorAll('.talk-term')).map(row => ({"
            "term:row.querySelector('.term-canonical').value,"
            "variants:row.querySelector('.term-variants').value}))"
        )
        print("Real Gemini suggestions in UI:", suggestions, flush=True)
        await stage.evaluate(
            "for(const row of document.querySelectorAll('.talk-term')) {"
            "const term=row.querySelector('.term-canonical').value;"
            "if(term==='Jim Hall') row.querySelector('input[type=checkbox]').click();"
            "if(term==='FreeDOS') {const input=row.querySelector('.term-variants');"
            "input.value='Free DOS, Free-DOS'; input.dispatchEvent(new Event('input'));}}"
        )
        await stage.evaluate("document.querySelector('#apply-terms').click()")
        for _ in range(10):
            await asyncio.sleep(0.5)
            applied = httpx.get(f"{BASE}/api/stages/gran-sala/talk-prep", timeout=5).json()
            if applied["terms"]:
                break
        assert applied["terms"], applied
        assert all(term["canonical"] != "Jim Hall" for term in applied["terms"])
        assert httpx.get(f"{BASE}/api/stages/auditorio/talk-prep", timeout=5).json()["terms"] == []
        print("Applied after operator review:", applied, flush=True)

        async with (
            Page(new_tab(9239, BASE + "/audience/gran-sala")) as audience,
            Page(new_tab(9239, BASE + "/display/gran-sala?lang=both")) as display,
            Page(new_tab(9239, BASE + "/overlay/gran-sala?lang=es")) as overlay,
            websockets.connect("ws://127.0.0.1:8019/ws/stages/gran-sala/captions") as captions,
        ):
            await asyncio.sleep(1)
            await stage.evaluate(
                "document.querySelector('#source-mode').value='file';"
                "document.querySelector('#source-mode').dispatchEvent(new Event('change'))"
            )
            document = await command(stage, "DOM.getDocument", {})
            file_input = await command(stage, "DOM.querySelector", {
                "nodeId": document["root"]["nodeId"], "selector": "#test-file-input",
            })
            await command(stage, "DOM.setFileInputFiles", {
                "nodeId": file_input["nodeId"], "files": [str(SAMPLE)],
            })
            received = []
            async def collect_captions() -> None:
                async for message in captions:
                    received.append(json.loads(message))

            collector = asyncio.create_task(collect_captions())
            await stage.evaluate("document.querySelector('#start').click()")
            shared_views = False
            try:
                for _ in range(75):
                    await asyncio.sleep(1)
                    current = status()
                    if not shared_views:
                        values = [
                            await latest(stage, "#original p:last-child"),
                            await latest(stage, "#translated p:last-child"),
                            await latest(audience, "#captions p:last-child"),
                            await latest(display, "#display-original .display-text"),
                            await latest(display, "#display-spanish .display-text"),
                            await latest(overlay, "#overlay-caption"),
                        ]
                        if all(values) and "Waiting" not in values[3] and "Waiting" not in values[4]:
                            shared_views = True
                            print("Live outputs EN/ES:", values[:2], flush=True)
                            print("Gemini connections and viewers:", current["connections"], current["viewers"], flush=True)
                            print("Preparation controls disabled while running:", await stage.evaluate(
                                "document.querySelector('#apply-terms').disabled"
                            ), flush=True)
                            conflict = httpx.post(f"{BASE}/api/stages/gran-sala/talk-prep", json={"terms": []}, timeout=5)
                            print("Apply while running HTTP:", conflict.status_code, flush=True)
                            assert current["connections"] == 1
                            assert conflict.status_code == 409
                    if current["state"] in {"completed", "failed"}:
                        print("Stage final state:", current["state"], current["error"], flush=True)
                        break
            finally:
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
            assert shared_views, "A caption output did not receive the real stream"
            assert current["state"] == "completed", current
            final_english = [event["text"] for event in received if event["language"] == "en" and event["is_final"]]
            final_spanish = [event["text"] for event in received if event["language"] == "es" and event["is_final"]]
            print("Final caption counts EN/ES:", len(final_english), len(final_spanish), flush=True)
            print("Observed approved term excerpts:", [text for text in final_english
                if any(term["canonical"].casefold() in text.casefold() for term in applied["terms"])][:8], flush=True)
            print("Last English final:", final_english[-1] if final_english else None, flush=True)
            print("Last Spanish final:", final_spanish[-1] if final_spanish else None, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
