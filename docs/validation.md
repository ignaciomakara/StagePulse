# StagePulse final release validation

[README](../README.md) · [Architecture](architecture.md) · [Operations](operations.md)

Commit baseline: `e80cd57` (`fix: isolate stage console state per tab`). The final demo configuration has exactly **Gran sala** (`gran-sala`), **Auditorio** (`auditorio`), and **Sala Abasto** (`sala-abasto`).

This page separates measured repository evidence from manual checks and operator reports. The local challenge audio is ignored by Git. No latency, WER, accuracy, audience-capacity, or availability figure should be inferred from a check that did not measure it.

## Automated release checks

- Python unit suite: **40 tests passing**.
- JavaScript lifecycle tests: **2 passing**.
- Frontend JavaScript syntax checks: passing.
- Python `compileall` for backend and tests: passing.
- `git diff --check`: clean.

## Controlled measurements in the repository

- [Gate 7.5](../benchmarks/gate75.md) records three file and three browser-path runs with real Gemini Live Translate and the 60-second Nerdearla sample. The **three browser runs** measured first backend PCM receipt to first published caption. Their medians were **4.65 s English** and **4.80 s Spanish**. This origin is not speech onset; the numbers are neither a maximum nor an SLA. One browser run had a 16.995-second gap in usable raw Spanish output while audio and raw English continued.
- [Gate 7.6](../benchmarks/gate76.md) records file and browser diagnostics. One browser recovery-enabled run had sparse output in **both** languages despite continued audio and a connected provider. The optional translation-stall reconnect did not trigger in the real recovery-enabled runs; its benefit remains unproven, and it is off by default.

The benchmark documents include methods and per-run observations. Their ignored raw logs remain local and are not part of the public repository. Benchmark results do not measure speech-onset latency or general caption accuracy.

## Three-stage manual check — 2026-09-25

The release-blocker validation used a fresh StagePulse server, three independent Chrome Stage Console tabs, a real local `samples/nerdearla-freedos-60s.wav` upload in each tab, and the configured `gran-sala`, `auditorio`, and `sala-abasto` rooms. No mock caption path was used. This check was recorded during the release workflow; it did not produce a committed trace.

| Observation | Result |
| --- | --- |
| Gran sala Test File started first | `running`, `source_mode=test_file`, one recorded provider connection. |
| Auditorio selected in a second tab while Gran sala ran | Controls and Talk Prep were editable before start; its Test File reached `running` without refreshing either tab. |
| Sala Abasto selected in a third tab while the first two ran | Controls and Talk Prep were editable before start; its Test File reached `running` without a refresh. |
| Three rooms active together | Control Room showed all three `running/connected`; Stage Console received nonempty English and Spanish captions for each. Each room reported one provider connection during the check. |
| Gran sala stopped | Auditorio and Sala Abasto remained `running`; Talk Prep unlocked for Gran sala and remained locked for the running rooms. |

This supports **three concurrent Test File stages with Stage Console captions and Control Room visibility**. It does not establish a maximum supported stage count, simultaneous three-room Audience/Display/Overlay behavior, or a three-stage latency distribution. Provider quota and server/network capacity still apply. Earlier manual validation reported two independent stages end to end with real audio and shared viewers.

## Other manual/operator observations

Previous release checks reported: a Test File reaching Stage Console, Audience View, Venue Display, and overlay through the same worker and bus; five caption subscribers on one active stage without extra Gemini connections; public Audience View access over HTTPS/4G; recovery after a stage-browser reload; and real Gemini Smart Talk Prep suggestions followed by operator review. Those observations are not a capacity benchmark or a verified integration inside OBS, vMix, or Swapcard.

A prior 13-minute, 1-second Nerdearla run reportedly received one real Gemini GoAway, resumed with a second sequential connection, and ended on a planned stop without a reported stage error or dropped PCM. The project team also reports processing a complete approximately 32-minute Nerdearla talk end to end. **Neither long-run raw trace is committed**, so their timing and completeness cannot be independently re-audited from this checkout. They should not be presented as an SLA or proof of unlimited session length.

## Repeatable local checks

Run the current unit suite and JavaScript lifecycle checks from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
node tests\test_audience_lifecycle.mjs
node tests\test_display_lifecycle.mjs
```

These tests include isolated fixtures and do not replace a real Gemini run. Browser manual scripts target the current three-room demo configuration; isolated historical file fixtures may still use `main`. Chrome CDP, an appropriate audio device, and the ignored local sample are needed for relevant manual scripts. For the evaluator path, follow the [README Test File steps](../README.md#test-with-a-local-audio-file) with an authorized file containing audio.

## Release limitations

StagePulse has no authentication and uses one in-memory `CaptionBus` process. It does not claim population-level caption accuracy or zero-downtime recovery. vMix/OBS browser-source behavior is documented but was not tested inside those applications. The debug translation-stall recovery option is not enabled for production and was not proven during a real qualifying stall.
