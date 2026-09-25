# StagePulse

StagePulse is open-source live captioning infrastructure built for the **Nerdearla Vibeathon 2026**. At a conference, each stage browser captures its venue audio once. StagePulse transcribes the original English speech, translates it to Spanish, and distributes the same caption events to the stage console, audience phones, production control room, and broadcast overlays. Audience viewers do not start additional AI pipelines.

The project is a working local-event prototype. The English documentation is canonical; see [README.es.md](README.es.md) for the Spanish guide and [operations](docs/operations.md) for the short production checklist.

## Quick Start (Windows PowerShell)

```powershell
git clone https://github.com/ignaciomakara/StagePulse.git
cd StagePulse
Set-ExecutionPolicy -Scope Process RemoteSigned -Force
.\scripts\setup.ps1
# Open .env and add your GEMINI_API_KEY
.\scripts\doctor.ps1
.\scripts\start.ps1
```

Open `http://127.0.0.1:8000/stage`. To get real captions without configuring a microphone or Stereo Mix, choose a configured stage, select **Audio source → Test file**, choose a local Nerdearla or other audio/video file, and press **Start**. The file is sent through the same stage worker and Gemini pipeline as live input. FFmpeg and a valid Gemini API key are required. The sample audio used during development is not included in Git; provide your own authorized file. The [manual installation steps](#manual-installation-fallback) remain available.

## Product outputs

| Route | Use |
| --- | --- |
| `/stage` | Operator console on the stage computer or mini-PC |
| `/audience` | Attendee stage/session selector |
| `/audience/{stage_id}` | Mobile live captions for one stage |
| `/display/{stage_id}?lang=original`, `?lang=es`, or `?lang=both` | Venue TV, second monitor, or projector |
| `/overlay/{stage_id}?lang=original` or `?lang=es` | Transparent vMix/OBS browser source |
| `/control` | Production Control Room |

Open `/display/{stage_id}` on the stage's second monitor or venue display. It uses the same caption stream, reconnects automatically, and defaults to both languages. StagePulse uses **one AI pipeline per active stage, not per viewer**; the number of simultaneous viewers still depends on the server and network capacity.

## Requirements

- Windows and Python with `venv` (tested with Python 3.13); network access to the Gemini API.
- A valid `GEMINI_API_KEY` in a local `.env` file. Never commit this file.
- A browser with microphone and AudioWorklet support. Microphone capture needs a secure context: localhost on the stage computer or HTTPS.
- FFmpeg on `PATH` for the separate file transcription tool and file-backed stage runs. Browser microphone capture does not use FFmpeg.
- Authorized audio input at the venue. Challenge audio samples are intentionally excluded from Git.

## Manual installation fallback

Run these commands in PowerShell from the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally and set `GEMINI_API_KEY`. Start the server either manually or with the Windows helper:

```powershell
.\.venv\Scripts\python.exe backend\serve.py
# Or:
.\scripts\start.ps1
```

The start helper checks for `.venv`, `.env`, and a valid port number before starting `backend/serve.py`. It accepts `-HostAddress`, `-Port`, and `-Config`. Run `scripts/doctor.ps1` first to check dependencies, stage configuration, and port availability. The manual command remains available and shows errors directly.

## Configure stages and terminology

Edit `config/stages.gate3.json`. Every configured stage gets its own `StageWorker` and Gemini Live Translate pipeline. Add another stage by adding a JSON entry, with no application-code change. The `audio_file` path supports file-backed checks; Stage Console replaces that source with the selected browser audio input when started.

An optional `terminology` map applies explicit word-boundary replacements to captions before publication, separately for each caption language:

```json
{
  "id": "main",
  "name": "Main Stage",
  "source_language": "en",
  "target_language": "es",
  "audio_file": "../samples/nerdearla-freedos-60s.wav",
  "terminology": {
    "en": {"Word Perfect": "WordPerfect"},
    "es": {"Word Perfect": "WordPerfect"}
  }
}
```

The example is configured only for `main`; a stage without `terminology` retains its original caption text. This is deterministic text replacement, not another model or fuzzy matching.

### Talk-aware terminology preparation

Before a talk, use **Talk preparation** in Stage Console to enter its title, speaker, and description. **Suggest terminology** makes one optional Gemini text request for up to 15 likely named terms and variants. Review, edit, enable, remove, or manually add terms, then press **Apply to stage**. Suggestions never apply automatically. StagePulse adds approved terms to the existing stage-specific `TerminologyNormalizer` before Start; this does not add another model request to the live caption path. Preparation is read-only while that stage runs. Applied terms remain in memory until the server restarts, and configured terminology remains available. This is an explicit spelling aid, not a measured accuracy improvement.

## Run a conference stage

1. Open `http://127.0.0.1:8000/stage` on the stage computer. Choose the stage and audio input, then press **Start**. The console shows original and Spanish captions.
2. Open `/control` to watch every configured stage, including audio, provider, connection, error, caption, and subscriber information.
3. Share the QR or copied link to `/audience/{stage_id}`. Audience viewers can switch **caption language** between Original and Spanish.
4. Add `/overlay/{stage_id}?lang=original` or `?lang=es` to the broadcast system. In vMix, use a 1920×1080 Browser Input; in OBS, use a 1920×1080 Browser Source. The overlay has a transparent background and places captions near the lower safe area.
5. Press **Stop** in Stage Console when the stage ends.

Stage Console, Audience Hub, Audience View, and Control Room use the same **UI language** preference (English/Español), saved in browser `localStorage`. It does not change the caption language. The display and overlay have no visible controls. The [operations guide](docs/operations.md) covers the full checklist and basic troubleshooting.

StagePulse can be embedded as a public HTTPS webview/iframe in an event platform. Use the `/audience` hub as the stable entry point; attendees can then open their stage. The event platform must permit embedding and WebSocket connections to the StagePulse origin. This is a generic browser embed, not an event-platform integration.

## LAN audience links

The QR uses the origin of the incoming request unless `STAGEPULSE_PUBLIC_BASE_URL` is set. Put this value in the local `.env` file or in the server process environment; the process environment takes precedence. A QR generated from localhost cannot be opened from another device; Stage Console warns about this. For LAN phones, use an origin they can reach:

```powershell
$env:STAGEPULSE_PUBLIC_BASE_URL = "http://192.168.1.20:8000"
.\.venv\Scripts\python.exe backend\serve.py --host 0.0.0.0
```

Replace the example IP with the stage computer's real LAN address. The variable must be an HTTP(S) origin without a path. Microphone access from another computer generally needs HTTPS; localhost remains suitable on the stage computer. StagePulse currently has no authentication, so keep the server on a trusted network. The QR is generated locally without an external QR service.

## Reliability and validation evidence

Live Translate uses session resumption handles and sliding-window context compression. StagePulse rotates the Gemini connection on GoAway and retries unexpected disconnects with bounded backoff while keeping the same stage worker and caption bus. During a reconnect, it buffers at most 102,400 bytes (3.2 seconds) of PCM, discards the oldest unsent frames if full, and does not replay frames whose delivery is uncertain. The browser input has its own 102,400-byte limit. No audio or captions are persisted.

Earlier real-audio validation exercised two simultaneous stages. A 13-minute-and-1-second continuous Nerdearla audio run received one GoAway with 50 seconds remaining, resumed onto a second connection, and ended on the planned stop with no reported error or dropped PCM. A separate production-view run showed five caption subscribers on one active stage without extra Gemini connections; closing and reopening a viewer changed the subscriber count without changing the provider connection count. These runs do not establish zero downtime, unlimited session length, or a general accuracy figure.

For a controlled reliability check, `backend/serve.py` and `backend/run_stages.py` accept `--debug-reconnect-after SECONDS`. It is off by default and should be used only for testing.

## Other tools and limits

`backend/transcribe_file.py path\to\audio-or-video-file` is the separate Gate 1 utility. It uses FFmpeg to send mono, 16-bit, 16 kHz PCM to Gemini Live and prints interim and final original-language transcripts. Its long-session behavior is separate from the stage translation provider.

StagePulse currently runs as one server process with an in-memory caption bus. It has no authentication, database, historical caption playback, or cross-process distribution. Browser-source behavior in vMix and OBS is documented but has not been tested inside those applications. The Gemini model is a preview dependency and requires available API access and quota. See [architecture](docs/architecture.md) for component boundaries.

## License

Apache-2.0; see [LICENSE](LICENSE).
