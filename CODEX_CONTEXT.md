# CODEX CONTEXT - EchoesEngine

## Canonical Folder

`C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine`

Do not use `C:\EchoesEngine` as the working folder. It is an old incomplete scaffold.

## Current Active Runtime

- C++ server builds successfully.
- Windows scheduled task `EchoesEngineDaemon` is installed and runs the daemon.
- Active local API listens on `127.0.0.1:8080`.
- FFmpeg is installed at `C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\tools\ffmpeg\ffmpeg.exe`.
- A verified render exists from previous validation: `renders\job-18\video.mp4`.

## Prep Components Added But Not Active

- `db/schema.sql`: dormant PostgreSQL schema for future users/jobs/swarm state.
- `agents/adaptive-orchestrator.ts`: dormant orchestrator. Running it only prints an init message.
- `agents/swarm-coordinator.ts`: dormant swarm registry/load balancer. Running it only prints an init message.
- `docker-compose.yml`: dormant local Postgres/Redis stack. It starts only if explicitly invoked.
- `package.json`: minimal package metadata/scripts for dormant TypeScript utilities.

## Safety Rules

- Do not replace `main.cpp`, `CMakeLists.txt`, or `CMakePresets.json` without a reviewed diff.
- Do not touch `renders/` or `jobs/` unless the user explicitly asks.
- Do not run destructive bootstrap scripts that delete or recreate project roots.
- Treat Qwen outputs as blueprints to review, not as automatic truth.
- New v2.4 components must be dormant by default until tested and explicitly activated.

## Next Roadmap

1. Keep active C++ daemon stable.
2. Add future infrastructure and agents behind dormant/manual activation.
3. Later implement gateway, GPU router, S3 sync, OTel federation, then public API/marketplace.

## Target Render Pipeline

Current authoritative runtime remains the C++ daemon on `127.0.0.1:8080`.

Future pipeline target:

```text
[Daemon C++ :8080] <- unchanged, authoritative
       ↓ HTTP POST /generate
[Gateway/Router TS] -> routes toward GPU worker
       ↓
[Diffusion Worker Python :8081] -> LTX-Video / CogVideoX + audio conditioning
       ↓
[Post-Processor Python :8082] -> RIFE 60fps + RealESRGAN 2x + FFmpeg sync
       ↓
[QC Guard TS] -> validation -> archive S3/local -> notification
```

Implemented prep so far: dormant gateway/router facade, dormant telemetry bridge, dormant QC guard, dormant orchestrator, dormant swarm coordinator, dormant DB schema.
