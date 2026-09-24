# StagePulse — Agent Rules

## Mission

Build an open-source real-time captioning infrastructure for live conferences.

A stage browser captures the venue audio input. StagePulse transcribes and translates it once per stage, then distributes the same caption stream to the venue display, mobile audience and broadcast integrations.

## Priority order

1. Hackathon rules and compliance
2. Working end-to-end functionality
3. Reliability during a live event
4. Evidence for the judging criteria
5. Simplicity
6. Visual polish

## Non-negotiable rules

- Never commit secrets, API keys or .env files.
- Never fake required hackathon functionality.
- Never fabricate latency, accuracy, viewer counts, health metrics or benchmarks.
- Required features must use real audio and real model responses.
- Keep changes small and test them before moving on.
- Do not introduce a database, Redis, Kubernetes or other infrastructure unless a concrete requirement needs it.
- Do not refactor working code without a clear reason.
- Do not silently break an existing API or working flow.
- Errors must be visible and actionable; do not swallow failures.
- Stage count must not be hardcoded.
- Adding another stage should be configuration, not new application code.
- AI processing is per stage, not per viewer.
- Audience clients consume shared caption events.
- Provider disconnections and provider session limits must be recovered without normal operator intervention.
- Reloading a stage browser should restore the active stage context wherever technically possible.

## Hackathon core

StagePulse must demonstrate:

- real audio input
- live original-language transcription
- live English-to-Spanish translation
- visible captions
- at least two real simultaneous stages
- straightforward scaling to more stages
- open-source deployment and documentation

## Judging criteria

Every substantial feature should improve at least one:

- Quality
- Latency
- Scalability
- Ease of deployment / operation
- Innovation

## Development workflow

For each task:

1. Inspect existing code.
2. Make the smallest viable change.
3. Run the relevant test or real execution path.
4. Report what changed.
5. Report any known limitation honestly.
6. Commit only a working checkpoint.

Working functionality beats architecture elegance.
