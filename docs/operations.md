# StagePulse operations

This is the short production checklist. Installation and limits are in the canonical [README](../README.md); the Spanish counterpart is [operations.es.md](operations.es.md).

1. **Configure a stage.** Add its ID and name to `config/stages.gate3.json`. Add optional `terminology` rules only for explicit corrections. Confirm `.env` contains `GEMINI_API_KEY`. Stage IDs appear in audience and overlay URLs.
2. **Choose audio input.** Run `.\scripts\start.ps1` (or `.\.venv\Scripts\python.exe backend\serve.py`). On the stage computer open `/stage`, select the stage, enable audio devices, and choose the venue input. Use localhost or HTTPS for browser microphone access.
3. **Start.** Press Start once. Confirm Stage Console reports audio streaming, then a running stage and a connected provider. Original and Spanish captions should appear as the speaker talks.
4. **Watch Control Room.** Open `/control`. Check stage status, browser connection, recent audio, provider connection, connection/reconnect counts, last caption, and last error. A brief provider reconnect may finish between one-second refreshes; the counters remain visible afterward.
5. **Share Audience QR.** Confirm the URL shown on Stage Console is reachable by phones. If it says localhost, set `STAGEPULSE_PUBLIC_BASE_URL` in `.env` or the process environment to the real LAN or HTTPS origin, then restart the server. Share the QR or copy the link. Viewers can choose caption language independently of UI language.
6. **Add broadcast overlay.** Use `/overlay/{stage_id}?lang=original` or `?lang=es`. Add it as a 1920×1080 Browser Input in vMix or Browser Source in OBS. It has a transparent background and no controls. Check the text in the production preview before going live.
7. **Stop.** At the end of the talk, press Stop in Stage Console. Confirm Control Room shows the stage stopped. Do not close the stage browser as a substitute for a planned stop.
8. **Troubleshoot.** If devices are missing, check browser microphone permission, the selected input, and secure context. If phones cannot open the QR, check the public origin, LAN address, firewall, and network reachability. If captions stop, read the stage's `last_error`, provider state, and audio timestamp in Control Room. Gemini connection recovery is automatic and bounded; persistent failures require checking API access/quota and network connectivity. Keep the browser open during a recoverable provider reconnect.

StagePulse has no authentication; operate it on a trusted network. The overlay and audience views use shared caption events and do not start additional Gemini sessions.
