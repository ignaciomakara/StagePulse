# Operating StagePulse

[English](operations.md) | [Español](operations.es.md) · [Quick start](../README.md#quick-start-windows-powershell) · [Architecture](architecture.md)

This guide is for a controlled event or demo. StagePulse has no built-in authentication, TLS termination, persistent history, or multi-process coordination. Use a trusted network and arrange a reachable HTTPS origin with WebSocket forwarding if attendees connect over the internet.

## Before the event

1. Install Python 3.10+ and FFmpeg on `PATH`. From the repository root run `.\scripts\setup.ps1`, edit the local `.env` with `GEMINI_API_KEY`, then run `.\scripts\doctor.ps1` **before** starting the server. Doctor checks local setup and port availability; it does not test API-key validity or provider quota.
2. Confirm the IDs and names in [config/stages.gate3.json](../config/stages.gate3.json). The demo rooms are `gran-sala`, `auditorio`, and `sala-abasto`. Add or change rooms in JSON, then restart the server. The ignored sample audio path is needed only for file-backed command-line runs, not browser Live input or Test File uploads.
3. If attendees will scan QR codes, set `STAGEPULSE_PUBLIC_BASE_URL` in `.env` (or the server process environment) to their reachable HTTP(S) **origin**, without a path. Process environment takes precedence. Restart after changing `.env`. A localhost URL works only on the host. For remote public access, arrange HTTPS and WebSocket forwarding separately; StagePulse does not bundle either.
4. Start the server with `.\scripts\start.ps1`. The default bind is `127.0.0.1:8000`; use `-HostAddress` and `-Port` only when your network setup requires them. Open `http://127.0.0.1:8000/stage` on the stage computer.

If PowerShell blocks helper scripts, run `Set-ExecutionPolicy -Scope Process Bypass -Force` in **that PowerShell process**, then retry. Do not change the machine-wide execution policy for this setup.

## Run a stage

| Step | Operator action |
| --- | --- |
| Prepare | Select the room in `/stage`. Optionally enter a talk title, speaker, and abstract. Ask Gemini for terminology suggestions, **review/edit** them, then apply. Suggestions never apply automatically; Talk Prep locks only while that room runs. |
| Capture | Choose **Live input** and an enabled audio device, or **Test file** and an authorized file with a decodable audio stream. Test File uses FFmpeg and accepts `.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.mp4`, `.mov`, or `.webm` up to 100 MiB. Only one source can run per room. |
| Start | Press **Start** once. Confirm the selected room becomes running, audio is recent, provider is connected, and original and Spanish captions appear. Different Stage Console tabs can operate different rooms. |
| Distribute | Share the Audience View URL/QR after checking it on an attendee device. `/audience` lists rooms. Open `/display/{stage_id}?lang=both` on a venue screen; use `original` or `es` to show one language. Add `/overlay/{stage_id}?lang=original` or `?lang=es` as a transparent browser source and inspect its preview. vMix/OBS compatibility is by browser-source design, not an in-application validation claim. |
| Monitor | Open `/control`. Review state, audio freshness, provider, viewers, connection/reconnect counts, session age, last caption, and last error for **each** room. Audience and display views share captions and do not open Gemini connections. |
| Stop | Press **Stop** for the selected room. Verify its state changes without stopping other rooms. Restart it with Start when needed; no caption history from the previous run is replayed. |

Browser Live input needs a secure context (localhost or HTTPS). A stage browser reload can reconnect its audio socket to an active stage within the server's recovery window; leave the tab open during a recoverable provider reconnect. Test File uploads are temporary and removed after normal completion or explicit stop. A process crash may leave a temporary file for operating-system cleanup.

## Read health signals

| Signal | Meaning and action |
| --- | --- |
| Audio recent / provider connected | The backend is receiving recent audio and the Gemini session is connected. If either is absent, inspect the selected device, browser permission, network, and `last_error`. |
| Translation OK | Recent audio and raw English/Spanish output support a healthy translation signal. It is not an accuracy or completeness score. |
| Translation delayed | The detector observed continuing audio and English output without recent usable Spanish. Check whether Spanish resumes and inspect provider/error state. The debug-only translation-stall reconnect option is **off** in normal operation and has no proven production benefit. |
| No translation label | There is not enough current evidence for either status; it does not prove that translation is healthy or failed. |
| Connections / reconnects | Cumulative counts for a stage. A count above one can reflect sequential recovery, not concurrent Gemini pipelines. |
| Audience reconnect | On foreground return, Audience View reconnects and gets the latest available caption per language. It does not replay captions missed in the background. |

Gemini GoAway and unexpected disconnections trigger bounded sequential recovery in the live provider. During gaps, buffering is bounded and old unsent audio may be dropped; StagePulse cannot guarantee zero missed speech. Keep an eye on `last_error` and whether captions resume.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| PowerShell blocks a script | Use `Set-ExecutionPolicy -Scope Process Bypass -Force` in the current shell, then rerun it. |
| FFmpeg missing or cannot run | Install FFmpeg, put `ffmpeg.exe` on `PATH`, reopen the shell, and rerun `setup.ps1` / `doctor.ps1`. |
| Media file has no audio stream | Choose a file with an audio track and audible speech. An allowed extension alone does not guarantee decodable audio; read the Stage Console/Control Room error. |
| Gemini access or quota error | Confirm `GEMINI_API_KEY` is present in local `.env`, check model access/quota and connectivity, then inspect `last_error`. Doctor checks presence, not validity. Do not expose the key while diagnosing. |
| Temporary Spanish delay | Check `Translation delayed`, recent English/audio, provider state, and whether Spanish resumes. A short gap alone does not justify claiming lost captions or enabling debug recovery. |
| Old bookmark/tab uses a removed stage ID | Open `/audience` or `/stage` again, select a configured room, and replace old bookmarks or QR codes. Stage-specific routes for removed IDs return 404. |
| Port 8000 already in use | Stop the existing listener or run `doctor.ps1 -Port 8001` and `start.ps1 -Port 8001`; use that port in the audience origin too. |
| Phone cannot open QR | Check the URL shown in Stage Console, reachable origin, network/firewall, HTTPS setup, and WebSocket forwarding. For a LAN URL, start with `start.ps1 -HostAddress 0.0.0.0`; the default listener accepts only localhost. A localhost QR is local to the host computer. |

The server is one in-memory process. Do not start a second instance expecting shared room state. For validated behavior and known evidence gaps, see [validation](validation.md) and the [README](../README.md#validation-and-measured-results).
