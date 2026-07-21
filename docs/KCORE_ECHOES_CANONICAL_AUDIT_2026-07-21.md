# K-Core / Echoes Canonical Audit — 2026-07-21

## Executive decision

- **EchoesEngine is the canonical native multimedia engine.**
- **Echoes Audio Core is the currently proven native processing path.**
- **Echoes Cinema Core is a real but incomplete video-generation framework already present in the repository.**
- **EchoesStudio is the public/client interface layer.**
- **k-core-operational-control is the operator control plane, not the DSP or render engine.**
- DreamGirl/DreamVerse may consume EchoesEngine, but must not become the source of truth for the engine.

## Verified reality

### EchoesEngine — Audio Core

**REAL**
- C++20/MSVC project with a static engine library and executable harness.
- `EchoesEngine::process(AudioBuffer&)` and interleaved float processing are wired into `CoreAudioPipeline`.
- Current public API exposes gain, gate, compressor, EQ, AI model loading, AI pitch/timbre/breath controls, emotional states, BPM, stats, and callbacks.
- The runtime has already been used by a Node adapter to process a real WAV and produce a valid float WAV output.

**PARTIAL**
- README still calls the project a scaffold and is stale relative to the actual v0.5 hardened pipeline.
- Public/local services are fragmented across ports 3000, 4000, 8080, 8081, and 8082.
- Several TypeScript agents are intentionally dormant or dry-run.

**MISSING / NOT PROVEN**
- Reproducible CI build on a clean Windows runner.
- Golden audio regression tests.
- Objective DSP A/B/C proof beyond format conversion.
- Stable versioned job API shared by EchoesStudio, DreamGirl, and K-Core.
- Production artifact manifest and signed release package.

### EchoesEngine — Cinema Core

**PARTIAL**
- The root CMake graph already includes `scene`, `camera`, `render`, `ai`, `ai_prompt`, `export`, `api`, and `platform` modules.
- `ECHoesEnginePro` links the scene, camera, render, AI, prompt-director, export, API, and platform libraries.
- EchoesStudio documents a video-oriented client contract using `/generate`, `/status/:jobId`, and `/video/:jobId`.
- A local worker control plane was created for diffusion and post-processing on ports 8081 and 8082.

**DORMANT / INCOMPLETE**
- The diffusion worker reports dependency preparation state and does not yet prove a real loaded video model.
- The post-processing worker is a control endpoint; complete FFmpeg/RIFE/ESRGAN production wiring has not been proven.
- Character continuity, shot consistency, camera-to-render execution, music synchronization, interpolation, upscaling, and final video assembly are not yet validated end-to-end.

**NOT MISSING**
- Video generation must not be classified as absent. The framework and module graph exist and the intended Luma-like workflow was started.

### EchoesStudio

**PARTIAL**
- Next.js/Tailwind public interface exists.
- Intended proxy routes are documented: `/generate`, `/status/:jobId`, `/video/:jobId`, `/api/payment`.
- Stripe payment route is documented as disabled/placeholder.

**BROKEN RISK**
- The UI contract implies finished video generation while the complete GPU path is not yet proven.
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
  -> Audio Core and Cinema Core workers
  -> QC and telemetry
  -> immutable job/event record

EchoesStudio / DreamGirl
  -> submit jobs through the same API
  -> never call local executables directly
  -> never own engine secrets in browser code
```

## Audio Core product targets

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

## Cinema Core product targets

1. Song structure and beat analysis
2. Story and visual treatment generation
3. Scene graph and shot list
4. Prompt Director per shot
5. Camera plan and motion language
6. Diffusion/video-model worker
7. Character and wardrobe continuity
8. Image-to-video and text-to-video modes
9. Music-synchronized clip assembly
10. Optical-flow interpolation
11. Upscaling and restoration
12. Color pipeline and final export
13. QC for codec, duration, resolution, black frames, frozen frames, audio sync, and corruption

## Immediate execution order

### Phase A — Make EchoesEngine reproducible

1. Refresh README to reflect actual audio and cinema architecture.
2. Add a clean Windows CI build.
3. Add CLI contract documentation.
4. Add deterministic WAV fixtures and regression tests.
5. Add DSP A/B/C verification.

### Phase B — Recover and prove Cinema Core

1. Inventory `scene`, `camera`, `render`, `ai`, `ai_prompt`, `export`, `api`, and `platform` modules.
2. Classify every module as `REAL`, `PARTIAL`, `MOCK`, `DORMANT`, `BROKEN`, or `MISSING`.
3. Compile `ECHoesEnginePro` separately from the standalone audio target.
4. Prove a deterministic non-AI render path first.
5. Replace the diffusion control stub with one real local model worker.
6. Generate one short real clip, then run interpolation, upscale, assembly, and QC.

### Phase C — Create one engine API

1. Define `/v1/health`, `/v1/jobs`, `/v1/jobs/:id`, `/v1/results/:id`.
2. Use job states: `QUEUED`, `PROCESSING`, `QC`, `FINISHED`, `FAILED`.
3. Separate `audio.process` and `video.generate` job contracts.
4. Emit telemetry and QC in a stable schema.

### Phase D — Make K-Core real

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
