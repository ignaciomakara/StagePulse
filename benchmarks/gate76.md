# Gate 7.6: translation tail latency and stall hardening

## Method and scope

The input for every real run was `samples/nerdearla-freedos-60s.wav`. FILE used `FileAudioSource` and FFmpeg `-re`. BROWSER played that WAV through Realtek speakers, captured Stereo Mix with Chrome `getUserMedia`, then used `BrowserAudioSource`, the stage worker, Gemini Live Translate, CaptionBus, Stage Console, Audience View, and Control Room. Diagnostics were enabled only for these runs. Recovery was disabled for the three BROWSER baseline runs and enabled only with the debug `--recover-translation-stall` flag for five more runs. Each run used one stage and a fresh server process. No other audio was deliberately played. The local `*.log` traces are ignored by Git.

All times below use a monotonic clock. The origin is the first PCM packet accepted by the backend. `Receipt-to-send` matches the same cumulative PCM byte offset at ingress and at the start of `send_realtime_input`; no provider buffer drops occurred in these runs. Its p95 and median include diagnostic logging overhead. Raw event gaps are between usable, nonempty Gemini transcription texts. No speech timestamp, WER, or semantic EN/ES alignment is inferred.

| Run | PCM chunks | First provider send (s) | First raw EN/ES (s) | Raw EN/ES | Max raw ES gap during PCM (s) | Receipt-to-send median/p95 (ms) | Confirmed translation stalls | Gemini connections |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FILE diagnostic | 704 | 1.108 | 2.823/3.037 | 58/57 | 2.005 | 0.106/0.213 | 0 | 1 |
| BROWSER baseline 1 | 680 | 0.491 | 3.453/3.675 | 59/58 | 1.989 | 0.566/1.275 | 0 | 1 |
| BROWSER baseline 2 | 687 | 1.191 | 3.875/4.103 | 59/59 | 1.271 | 0.571/1.289 | 0 | 1 |
| BROWSER baseline 3 | 681 | 0.458 | 2.301/2.492 | 60/59 | 1.988 | 0.614/2.066 | 0 | 1 |
| BROWSER recovery 1 | 676 | 0.490 | 3.465/3.700 | 57/56 | 1.999 | 0.555/1.103 | 0 | 1 |
| BROWSER recovery 2 | 691 | 1.214 | 3.627/4.772 | 59/48 | 2.498 | 0.563/1.735 | 0 | 1 |
| BROWSER recovery 3 | 681 | 1.217 | 3.620/3.806 | 59/59 | 1.266 | 0.579/1.771 | 0 | 1 |
| BROWSER recovery 4 | 684 | 0.468 | 22.615/24.769 | 5/5 | 16.458 | 0.570/1.504 | 0 | 1 |
| BROWSER recovery 5 | 685 | 1.204 | 3.837/4.025 | 59/59 | 1.260 | 0.546/1.582 | 0 | 1 |

All real runs recorded zero stage errors, zero reconnects, and zero requests to the translation-stall recovery option. The enabled flag made no request because none of those five runs met the detector's complete evidence requirements. Differences in raw counts or latency between baseline and recovery runs cannot be attributed to recovery when it never fired.

## FILE versus BROWSER packetization

The FILE diagnostic yielded mainly 2,730/2,732-byte chunks about every 83.5 ms. BROWSER yielded 3,200-byte chunks about every 100 ms. The FILE source had no queue; its provider buffer wait median was 0.024 ms. Across the three BROWSER baselines, source queue depth median was one frame, source queue wait median 0.524–0.542 ms, provider buffer wait median 0.028–0.030 ms, and backend receipt-to-send median 0.566–0.614 ms. The first send lag varied with Gemini connection setup: 1.108 s FILE and 0.458–1.191 s BROWSER. There is no measured, sustained one-second buffering after backend PCM receipt in the browser path.

The first browser PCM preceded a clearly active sample frame by about 0.39–0.47 s, using RMS >100 PCM units solely as a trace marker. In FILE the first frame already exceeded that marker. The first browser second's median RMS was about 0.7–0.8 units, while FILE started with active audio. This leading near-silence explains **part** of the previous first-PCM-to-caption difference. First raw EN varied from 2.301 to 3.875 s in the BROWSER baselines versus 2.823 s in this FILE diagnostic, so a fixed additional browser delay of one second was not reproduced. The remaining variation was not isolated to one cause.

First published EN was 3.349 s FILE versus 4.174, 4.562, and 4.677 s in the BROWSER baselines; first published ES was 3.491 s FILE versus 4.358, 4.762, and 3.947 s BROWSER. In BROWSER baseline 3, raw EN was early at 2.301 s but the first EN caption was not published until 4.677 s because the assembler had not yet received a unit long enough for its existing preview rule. This is a variable raw-to-caption assembly delay, not evidence of browser PCM buffering. No assembler threshold was changed.

## Detector definition and threshold

One translation stall requires all of these at an EN raw event: stage `running`; Live Translate provider `connected`; PCM accepted within 1.5 s and not ended; at least two raw EN events in the last 3 s since the last ES; a recent EN event; and at least **6 s** since the last raw ES. If no ES has arrived yet, the 6 s reference is the first raw EN. The check is deferred to the next event-loop turn so EN and ES delivered in one Gemini response cannot cause a premature reconnect. `translation_stall_active` is true only while the evidence remains current. A counter and start timestamp record each confirmed episode. Silence without continuing EN, provider disconnection, stopped stages, and file drain do not count.

Six seconds is a conservative, configurable starting value: in the six controlled Gate 7.5 runs, five maximum live raw ES gaps were about 2 s and one was 16.995 s. Retrospective application of this rule marked only that abnormal BROWSER run, at 13.730 s after its last ES, once two EN events were again close together. It marked none of the five normal runs. This small sample does not establish a universal threshold or prove absence of false positives. The flag `--translation-stall-seconds` allows adjustment; the default is six seconds.

The existing health API now exposes `translation_stall_active`, `translation_stall_started_at`, `translation_stall_count`, `age_last_raw_en`, `age_last_raw_es`, and `translation_status`. Control Room renders **Translation OK** only when recent audio, EN, and ES support it, and **Translation delayed** only for a confirmed stall. When evidence is absent it shows neither status. The browser runs showed the OK state; no real run produced the delayed state. Unit tests cover the delayed API state; locale-key consistency and JavaScript syntax checks cover the UI change.

## Recovery experiment and important limitation

`--recover-translation-stall` is debug-only and off by default. On a confirmed stall it requests one sequential rotation of the current Live connection through the existing reconnect/session-resumption mechanism. The stage worker and stage ID are retained; the request is limited to one per stage run, with a 30 s cooldown guard. No parallel Live session is opened by the code path. A synthetic unit test confirmed that a request closes the current session before another could open. **No real stall meeting the definition occurred in the five recovery-enabled runs**, so there is no real evidence that recovery returns ES sooner, reduces gaps, preserves EN, or avoids duplicate captions after a stall. Automatic recovery should remain disabled in production.

BROWSER recovery 4 exposed a distinct failure: PCM continued at normal intervals and 684 chunks were sent to Gemini, with nontrivial RMS throughout the 60 s, but the connected session delivered only five raw EN and five raw ES events. First raw EN arrived at 22.615 s; the first published EN caption appeared at 40.604 s. Audience was blank through the 40 s checkpoint. The longest raw gaps were 17.989 s EN and 16.458 s ES. This was **not** a translation-only stall because EN was absent too; the detector correctly did not trigger. The cause of this provider/session output deficit is unknown. It shows that translation-stall health does not measure overall caption coverage. No recovery action occurred, so this deficit cannot be attributed to the debug flag.

## Validation and reproduction

Seven synthetic detector/lifecycle tests cover positive detection, initial ES absence, silence, audio end, disconnection, single recovery request, and EN/ES in the same provider response. They are explicitly separate from real Gemini evidence. The full unit suite, `compileall`, JavaScript syntax checks, and `git diff --check` were run after implementation.

```powershell
.venv\Scripts\python.exe tests\manual_gate75_file.py
.venv\Scripts\python.exe backend\serve.py --diagnostics
# Start isolated headless Chrome CDP on port 9236, then in another terminal:
$profilePath = Join-Path $env:TEMP 'stagepulse-gate76-chrome'
Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList @('--headless=new','--remote-debugging-port=9236','--remote-allow-origins=*',"--user-data-dir=$profilePath",'--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required','http://127.0.0.1:8000/stage') -WindowStyle Hidden
.venv\Scripts\python.exe tests\manual_gate76_browser.py
# For a separate debug recovery run, restart the server with:
.venv\Scripts\python.exe backend\serve.py --diagnostics --recover-translation-stall
.venv\Scripts\python.exe tests\manual_gate76_report.py --table benchmarks\gate76\file-diagnostic.log benchmarks\gate76\browser-baseline-1.log benchmarks\gate76\browser-recovery-1.log
```

The fixed WAV is embedded in the manual file and browser scripts. Each BROWSER run requires Chrome CDP on port 9236, Stereo Mix, and Realtek output device 20 on this test machine. Device indexes may differ elsewhere. These runs cannot estimate a population-level stall rate or prove recovery effectiveness.
