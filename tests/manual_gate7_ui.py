"""Check UI language selection in Chrome without starting a provider session."""

from __future__ import annotations

import asyncio

from manual_gate4_browser import Page, new_tab, targets


PORT = 9232
BASE = "http://127.0.0.1:8012"


async def main() -> None:
    stage_target = next(item for item in targets(PORT) if item.get("url") == f"{BASE}/stage")
    async with Page(stage_target) as stage:
        await stage.evaluate(
            "document.querySelector('#ui-language').value='es';"
            "document.querySelector('#ui-language').dispatchEvent(new Event('change'))"
        )
        await asyncio.sleep(1)
        print("Stage Start:", await stage.evaluate("document.querySelector('#start').textContent"), flush=True)
        print("Stage UI:", await stage.evaluate("document.querySelector('#ui-language').value"), flush=True)
        print("QR warning:", await stage.evaluate("document.querySelector('#audience-warning').textContent"), flush=True)
        audience_target = new_tab(PORT, f"{BASE}/audience/main")
        control_target = new_tab(PORT, f"{BASE}/control")
        async with Page(audience_target) as audience, Page(control_target) as control:
            await asyncio.sleep(2)
            print("Audience UI:", await audience.evaluate("document.querySelector('#ui-language').value"), flush=True)
            print("Audience caption label:", await audience.evaluate("document.querySelector('[data-i18n=captionLanguage]').textContent"), flush=True)
            print("Audience caption selection:", await audience.evaluate("document.querySelector('#language').value"), flush=True)
            print("Control UI:", await control.evaluate("document.querySelector('#ui-language').value"), flush=True)
            print("Control cards:", await control.evaluate("document.querySelectorAll('.stage-card').length"), flush=True)
            await audience.evaluate(
                "document.querySelector('#language').value='es';"
                "document.querySelector('#language').dispatchEvent(new Event('change'));"
                "document.querySelector('#ui-language').value='en';"
                "document.querySelector('#ui-language').dispatchEvent(new Event('change'))"
            )
            await asyncio.sleep(1)
            print("Audience UI after switch:", await audience.evaluate("document.querySelector('#ui-language').value"), flush=True)
            print("Caption selection after UI switch:", await audience.evaluate("document.querySelector('#language').value"), flush=True)
            print("Control UI after storage event:", await control.evaluate("document.querySelector('#ui-language').value"), flush=True)
            await audience.evaluate("location.reload()")
            await asyncio.sleep(2)
            print("Audience UI after reload:", await audience.evaluate("document.querySelector('#ui-language').value"), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
