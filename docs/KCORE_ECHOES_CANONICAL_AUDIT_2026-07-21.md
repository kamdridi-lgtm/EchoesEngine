# K-Core / Echoes Canonical Audit — 2026-07-21

## Executive decision

- **EchoesEngine is the canonical native audio engine.**
- **EchoesStudio is the public/client interface layer.**
- **k-core-operational-control is the operator control plane, not the DSP engine.**
- DreamGirl/DreamVerse may consume EchoesEngine, but must not become the source of truth for the engine.

## Verified reality

### EchoesEngine

**REAL**
- C++20/MSVC project with a static engine library and executable harness.
- `EchoesEngine::process(AudioBuffer&)` and interleaved float processing are wired into `CoreAudioPipeline`.
- Current public API exposes gain, gate, compressor, EQ, AI model loading, AI pitch/timbre/breath controls, emotional states, BPM, stats, and callbacks.
- The runtime has already been used by a Node adapter to process a real WAV and produce a valid float WAV output.

**PARTIAL**
- README still calls the project a scaffold and is stale relative to the actual v0.5 hardened pipeline.
- Public/local services are fragmented across ports 3000, 4000, 8080, 8081, and 8082.
- Worker control endpoints for diffusion/post-processing are control stubs, not real GPU generation workers.
- Several TypeScript agents are intentionally dormant or dry-run.

**MISSING / NOT PROVEN**
- Reproducible CI build on a clean Windows runner.
- Golden audio regression tests.
- Objective DSP A/B/C proof beyond format conversion.
- Stable versioned job API shared by EchoesStudio, DreamGirl, and K-Core.
- Production artifact manifest and signed release package.

### EchoesStudio

**PARTIAL**
- Next.js/Tailwind public interface exists.
- Intended proxy routes are documented: `/generate`, `/status/:jobId`, `/video/:jobId`, `/api/payment`.
- Stripe payment route is documented as disabled/placeholder.

**BROKEN RISK**
- Documentation claims video generation while the currently proven native engine path is audio processing.
- Engine URL contract is not yet versioned or canonical.

### K-Core Operational Control

**REAL**
- React/Vite/Express application exists.
- `/api/state` reads local JSON status files.

**MOCK / DANGEROUSLY MISLABELED**
- `/api/action` only logs and returns `SUCCESS`; it does not execute a real action.
- The app therefore behaves as a dashboard prototype, not an operational control plane.
- No authentication, authorization, audit trail, command allowlist, rollback, or signed agent protocol is present in the verified server implementation.

## Canonical architecture

```text
K-Core Control Plane
  -> authenticated, allowlisted commands
  -> versioned EchoesEngine API
  -> native EchoesEngine worker
  -> QC and telemetry
  -> immutable job/event record

EchoesStudio / DreamGirl
  -> submit jobs through the same API
  -> never call local executables directly
  -> never own engine secrets in browser code
```

## Non-negotiable modules

The following remain product targets and must be implemented or proven independently:

1. Mastering chain
2. QSound / spatial engine
3. Fatigue simulation (2h / 4h)
4. Micro pitch drift
5. Naturalness scoring
6. Mono safety analyzer
7. ONNX model manager
8. Neural scheduler
9. Multi-state renderer
10. Psychoacoustic engine
11. Stem exports: `dry`, `processed`, `spatial`, `full`

## Immediate execution order

### Phase A — Make EchoesEngine reproducible

1. Refresh README to reflect actual v0.5 architecture.
2. Add a clean Windows CI build.
3. Add CLI contract documentation.
4. Add deterministic WAV fixtures and regression tests.
5. Add DSP A/B/C verification.

### Phase B — Create one engine API

1. Define `/v1/health`, `/v1/jobs`, `/v1/jobs/:id`, `/v1/results/:id`.
2. Use job states: `QUEUED`, `PROCESSING`, `QC`, `FINISHED`, `FAILED`.
3. Separate audio processing from video generation.
4. Emit telemetry and QC in a stable schema.

### Phase C — Make K-Core real

1. Replace fake `/api/action` success responses with an allowlisted command registry.
2. Add authentication and role checks.
3. Add immutable audit events.
4. Add idempotency keys and kill switch.
5. Start read-only; enable mutations one command at a time.

## Status vocabulary

Only use:

- `REAL`
- `PARTIAL`
- `MOCK`
- `DORMANT`
- `BROKEN`
- `MISSING`

No component is called complete without a reproducible test and evidence artifact.
