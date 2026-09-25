# StagePulse

**Open-source real-time captioning infrastructure for live conferences.**

StagePulse is being built during the Nerdearla Vibeathon 2026.

The goal is simple: capture live audio from each stage once, transcribe and translate it in real time, and distribute the resulting captions to the venue screen, audience devices and live broadcast integrations.

## Current status

Work in progress.

## Local live transcription

Install FFmpeg and the Python dependencies, then add `GEMINI_API_KEY` to the
project's local `.env` file. The key is read locally and is never printed.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python backend/transcribe_file.py path\to\audio-or-video-file
```

The tool sends mono, 16-bit, 16 kHz PCM to Gemini Live in real time and prints
interim and finalized input transcriptions. The current live transcription
model supports sessions of up to 10 minutes.

## Browser stage and audience views

Install the dependencies, keep `GEMINI_API_KEY` in the local `.env`, and start the
server from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe backend\serve.py
```

Open `http://127.0.0.1:8000/stage` on the stage computer. Select the configured
stage and audio input, then press **Start**. The console shows original and
Spanish captions. Use **Stop** to end that stage's stream. Each stage has one
Gemini session while any number of audience tabs read the shared caption bus.

Open `http://127.0.0.1:8000/audience/main` for the example audience view;
replace `main` with another configured stage ID. Audience viewers can choose
Original or Español. The example stage IDs and names are in
`config/stages.gate3.json`; add stages there without changing Python code.

The stage browser needs a secure context for microphone access: use localhost
on the stage computer or HTTPS. For mobile viewers on the same network, start
the server with `--host 0.0.0.0` and open its LAN address. This minimal server
has no authentication; keep it on a trusted local network.

## Production views

- `/stage` captures the selected stage audio once and shows original and Spanish captions.
- `/audience/{stage_id}` shows shared captions with an Original/Spanish selector,
  connection status, automatic reconnect, and a Fullscreen button.
- `/control` polls existing stage status once per second and lists every configured
  stage, its actual provider/audio/caption state and subscriber count, and links
  to the stage, audience, and overlay views. Viewers do not open Gemini sessions.
- `/overlay/{stage_id}?lang=original` and `?lang=es` show the selected live
  caption on a transparent page. Captions clear after 15 seconds without an
  update. The overlay uses the same caption WebSocket as Audience View.

Stage Console shows a QR and copyable audience link. By default, its URL uses
the browser request's origin. A QR built from localhost works only on the stage
computer; Stage Console warns when this happens. For audience phones on a LAN,
set `STAGEPULSE_PUBLIC_BASE_URL` to the origin reachable from those phones,
for example `http://192.168.1.20:8000`, and run the server on a reachable
interface. This variable must be an HTTP(S) origin without a path. If using
HTTPS through a reverse proxy, set it to the public HTTPS origin. The QR is
generated locally and does not use a third-party QR service.

```powershell
$env:STAGEPULSE_PUBLIC_BASE_URL = "http://192.168.1.20:8000"
.\.venv\Scripts\python.exe backend\serve.py --host 0.0.0.0
```

In **vMix**, add a Browser Input using the full overlay URL, set its size to
1920×1080, and layer it over the program feed. In **OBS**, add a Browser Source
with the same URL, width 1920 and height 1080. Choose `lang=original` or
`lang=es` for the output. The overlay background is transparent and captions
sit in the lower safe area. Each overlay and audience viewer subscribes to the
same stage bus; adding or closing viewers does not create Gemini connections.

## Long-running Live Translate sessions

Live Translate enables session resumption and sliding-window context compression.
StagePulse retains the latest valid resumption handle in memory and rotates the
Gemini connection when GoAway arrives. Unexpected disconnects use short, bounded
retries. The stage worker and its caption subscribers remain in place during a
provider reconnect. The stage status API exposes connection and reconnect counts,
provider state, last audio/caption timestamps, GoAway details, and whether a
resumption handle exists; it never exposes the handle itself.

The provider-side reconnect buffer holds at most 3.2 seconds of PCM
(102,400 bytes). The browser ingress source has a separate 102,400-byte limit,
which the provider pump continues to drain during reconnects. If the reconnect
buffer fills, the oldest unsent audio is discarded
and counted in `dropped_audio_bytes`. A frame already taken for sending is not
replayed if delivery becomes uncertain, avoiding duplicate audio at the cost of
a possible short gap. Audio and captions are not persisted.

For a controlled reliability check, `backend/run_stages.py` and `backend/serve.py`
accept `--debug-reconnect-after SECONDS`. This test-only option forces one
provider reconnect after the specified delay and is off by default. The
long-audio file configuration is `config/stages.gate5-soak.json`.

## Core goals

- Real-time original-language transcription
- Real-time English-to-Spanish translation
- Multiple simultaneous stages
- Audience web captions
- Stage display
- Broadcast overlay
- Automatic recovery from normal streaming failures
- Simple open-source deployment

## License

Apache-2.0

