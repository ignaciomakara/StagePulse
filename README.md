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

