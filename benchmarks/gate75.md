# Gate 7.5: translation coverage and latency

## Method

All six measured runs used `samples/nerdearla-freedos-60s.wav` and the configured `gemini-3.5-live-translate-preview` pipeline. File runs used `FileAudioSource` and FFmpeg `-re`. Browser runs played the WAV through Realtek speakers, captured Stereo Mix in headless Chrome using `getUserMedia`, and passed PCM through `BrowserAudioSource`. The browser test observed Stage Console and Audience View. No other audio was deliberately played. `--diagnostics` was enabled only for these tests. Raw logs remain local in the ignored `benchmarks/gate75/*.log` files.

Times are seconds from the **first PCM packet accepted by the backend**, measured with `time.monotonic()`. Browser playback starts after the browser begins sending PCM, so its times include a short leading silence and must not be interpreted as model-only latency or directly compared with file times. Published character totals include repeated preview text. Publication gaps are consecutive events in the same language, including the final post-audio flush. A final caption is a StagePulse-closed unit, not necessarily an explicit Gemini final signal. `provider_to_bus` is measured only for a caption causally emitted by the same callback and includes diagnostic logging overhead. EN and ES events have no proven semantic pairing.

## Measured runs

All first-event and gap values below are seconds. `Preview` and `Final` are published event counts. All six runs had no recorded stage error or provider reconnect.

| Run | Raw EN/ES | First raw EN/ES | First published EN/ES | Preview EN/ES | Final EN/ES | Median gap EN/ES | Max gap EN/ES | Published chars EN/ES |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FILE 1 | 58/58 | 2.936/3.147 | 3.477/3.849 | 21/21 | 12/13 | 1.877/1.990 | 9.391/9.060 | 1653/1606 |
| FILE 2 | 58/58 | 2.792/2.963 | 3.333/3.654 | 23/20 | 12/14 | 1.918/1.990 | 9.296/9.015 | 1700/1618 |
| FILE 3 | 58/58 | 3.065/3.266 | 3.683/3.960 | 21/22 | 12/13 | 1.956/1.707 | 10.438/9.262 | 1601/1734 |
| BROWSER 1 | 58/57 | 3.867/4.097 | 4.509/4.797 | 21/17 | 11/13 | 1.240/1.248 | 11.576/17.497 | 1623/1367 |
| BROWSER 2 | 58/58 | 3.884/4.076 | 4.649/4.785 | 21/20 | 12/14 | 1.936/1.965 | 4.049/3.998 | 1629/1541 |
| BROWSER 3 | 59/59 | 3.753/3.905 | 4.737/5.134 | 21/21 | 11/13 | 1.973/1.981 | 4.100/4.003 | 1564/1611 |

First published ES minus first published EN was 0.372, 0.321, 0.277 seconds for FILE and 0.288, 0.136, 0.397 seconds for BROWSER. These differences compare the first events only; they are not matched translations. Median callback-to-bus publication time was 0.108–0.116 ms for FILE and 0.473–0.638 ms for BROWSER. The trace itself adds overhead, so these are diagnostic observations rather than production latency guarantees.

The three FILE runs each accepted 1,920,000 PCM bytes across about 59.5 seconds. BROWSER runs accepted 2,252,800–2,259,200 PCM bytes across about 69.7–70.0 seconds, including browser lead-in and trailing silence. Largest PCM packet gap in the measured BROWSER runs was 0.150–0.167 seconds.

## Localization

In BROWSER 1, the raw ES gap was **16.995 seconds** (39.546–56.541 seconds from first PCM). During it, the backend received 169 PCM packets and Gemini delivered 8 raw EN events, but no usable raw ES text. The ES publication gap was 17.497 seconds. Every raw ES text event reached the assembler; the bus published the units the assembler emitted. This is an intermittent absence/delay of usable Gemini output transcription, not an observed loss in browser ingest, assembler-to-bus delivery, or Audience rendering. BROWSER 2 and 3 did not repeat this gap; their largest ES raw gaps were 1.999 and 1.287 seconds. No provider disconnect or reconnect was recorded. The exact reason inside Gemini is unknown.

The FILE maximum publication gaps occurred after the 60-second PCM input ended, before the final flush at the provider drain deadline. There were no raw events in those windows; they do not show a missing live translation during speech.

The configured `response_modalities=["AUDIO"]`, `input_audio_transcription`, `output_audio_transcription`, and `translation_config.target_language_code="es"` match the current [Google Live Translate documentation](https://ai.google.dev/gemini-api/docs/live-api/live-translate) and the installed `google-genai 2.25.0` SDK fields. The [model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-live-translate-preview) still lists the configured model. No model, provider setting, or assembler threshold changed.

Both published languages contained `DOS` and `WordPerfect` in every measured run. This is an occurrence check, not an accuracy score. The existing terminology normalizer may have changed `Word Perfect` to `WordPerfect` in published captions.

## Late joiner

Before this change, `CaptionBus.subscribe()` registered a new subscriber without sending any existing caption, so Audience View was blank until the next live event. It now queues only the latest in-memory event per language before subscribing to the live stream. The latest state is cleared when a stage starts again. Audience View shows `Waiting for captions...` or `Esperando subtítulos...` according to UI language when no caption is available. In all three measured BROWSER runs, a page opened during playback immediately displayed an ES caption while the Gemini connection count remained one; subsequent captions continued live. The unit and WebSocket tests cover snapshot delivery and no replay of the full history. There is no stored history or extra Gemini session.

## Before/after scope and limitations

The translation pipeline received **no behavioral correction**, so a translation latency or coverage before/after claim would be unsupported. The verified behavioral before/after is limited to late joining: previously a new subscriber waited for the next event; now it receives the latest caption immediately. The three FILE and three controlled BROWSER runs above were all measured after that snapshot change. An earlier BROWSER pilot (`browser-1.log`) included several seconds of setup silence before WAV playback and was excluded from the controlled table.

| Scope | Before | After |
|---|---|---|
| FILE translation metrics | No pre-change diagnostic measurements | Three measured runs above; translation behavior unchanged |
| BROWSER translation metrics | No pre-change controlled measurements | Three measured runs above; translation behavior unchanged |
| Audience late join | Empty until the next live event | Latest available caption per language delivered on subscribe, then live events |

The browser test used a physical Realtek output and Stereo Mix on this machine. It does not prove behavior on another audio device, in another network environment, or for arbitrary live speech. No WER or speech-to-caption latency was calculated.

## Reproduction and validation

```powershell
.venv\Scripts\python.exe tests\manual_gate75_file.py
.venv\Scripts\python.exe backend\serve.py --diagnostics
# In another terminal, with Stereo Mix available:
$profilePath = Join-Path $env:TEMP 'stagepulse-gate75-chrome'
Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList @('--headless=new','--remote-debugging-port=9235','--remote-allow-origins=*',"--user-data-dir=$profilePath",'--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required','http://127.0.0.1:8000/stage') -WindowStyle Hidden
.venv\Scripts\python.exe tests\manual_gate75_browser.py
.venv\Scripts\python.exe tests\manual_gate75_report.py --table benchmarks\gate75\file-1.log benchmarks\gate75\browser-2.log
```

The full suite passed with 25 tests on its final run; `compileall`, JavaScript syntax checks, and `git diff --check` also passed. One preceding full-suite run intermittently raised `CancelledError` while an existing WebSocket test closed; that test and the full suite passed on immediate rerun, with no production-path failure observed. The benchmark scripts make real Gemini requests and do not mock transcription or translation.
