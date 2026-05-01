# EchoesEngine Checkpoint - 2026-04-30

## Canonical local source folder
C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine

This is the clean source folder the user previously provided. It contains the advanced C++/JUCE source tree and the MSVC 2026 build script.

## Evidence/render archive folder
C:\Users\Administrator\.openclaw\tmp\EchoesEngine-canonical

This folder is not a clean Git working tree, but it contains logs, prior builds, jobs, and a large render archive. At the time of inspection it contained 641 render job directories.

## GitHub repository
https://github.com/kamdridi-lgtm/EchoesEngine.git

GitHub main currently has BUILD_MISSION.md but does not contain all local runtime/daemon scripts. Branch codex-echoesengine-canonical has the advanced build.bat pattern.

## Added operational scripts
- scripts\start-server.bat: builds EchoesEngine and starts the best available executable.
- scripts\daemon.bat: 24/7 restart loop with runtime logs.
- scripts\autojob-agent.ps1: optional local job submitter for continuous generation.
- scripts\install-daemon-task.ps1: installs/starts Windows scheduled task EchoesEngineDaemon.

## Build output target
%USERPROFILE%\echoes-build\echoesengine-canonical-vs2026-release

Expected outputs:
- Release\EchoesEngine.lib
- Release\ECHoesEnginePro.exe
- Release\EchoesEngine.exe
- EchoesPlugin_artefacts\Release\Standalone\EchoesEngine.exe
- optional VST3 artifact

## Next priorities
1. Run scripts\build.bat and confirm compile proof.
2. Run scripts\start-server.bat and verify HTTP API on port 8080.
3. Install scripts\install-daemon-task.ps1 only after build/server success.
4. Push the canonical operational scripts to GitHub once local behavior is proven.
5. Keep the render archive; do not delete the partial clone/archive unless explicitly approved.

## 2026-04-30 verification update
- Build succeeded with Visual Studio 18 2026.
- ECHoesEnginePro.exe started and listened on 127.0.0.1:8080.
- /generate accepted a test job after default WAV files were added.
- FFmpeg 8.1 installed locally at C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\tools\ffmpeg\ffmpeg.exe.
- job-18 reached finished status and produced renders\job-18\video.mp4.
- start-server.bat now sets conservative 24/7 defaults: max 1 concurrent job, auto-job interval 300 seconds, duration 15 seconds.


## 2026-05-01 daemon correction
- Scheduled task initially ran as SYSTEM, so %USERPROFILE% pointed to systemprofile.
- scripts\build.bat was corrected to use fixed build directory C:\Users\Administrator\echoes-build\echoesengine-canonical-vs2026-release.


## Update - 2026-05-01 00:38:13 -04:00

- Recovered disk after a failed full GitHub clone of kamdridi-lgtm/EchoesEngine; the clone failed because the remote repository tracks heavy `renders/` and `jobs/` artifacts.
- Deleted only the temporary failed clone C:\tmp\EchoesEngine-main and restored roughly 17 GB free disk.
- Restarted EchoesEngineDaemon; server verified listening on 127.0.0.1:8080.
- Added .env.example with local runtime, autojob, FFmpeg, and future cloud/autopilot variables.
- Current blocker for GitHub sync: GitHub app write access returns 403, and normal clone is unsafe until remote heavy artifacts are removed or sparse checkout is used.

## Update - 2026-05-01 00:54:50 -04:00

- Reviewed `C:\Users\Administrator\OneDrive\Bureau\echoesengine_report_2026-04-16_00-11-43.txt`.
- That report points to old `C:\EchoesEngine`, but it was only a small/incomplete scaffold: it had `app`, `engine`, `build`, `CMakeLists.txt`, and `run.bat`, but was missing `scripts`, `runtime`, `jobs`, `core`, `main.cpp`, `README.md`, `AGENTS.md`, `CMakePresets.json`, and `RELEASE_MANIFEST.json`.
- Canonical working folder remains `C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine`.
- Do not restart from `C:\EchoesEngine`; use it only as historical evidence unless the user explicitly asks to archive/delete it.



## Target Checkpoint Supplied By User - v2.4.0-multi-node-federation - 2026-05-01 01:06:17 -04:00

Status in canonical folder: NOT INSTALLED YET. This is a target/roadmap checkpoint provided by the user, not verified active code.

Expected components from the supplied checkpoint:
- `deploy/docker-compose.cluster.yml`: multi-node orchestration, GPU runtime, healthchecks, isolated network.
- `agents/cluster-sync.ts`: watch state/renders, sync to primary+secondary S3, DB catch-up, silent fallback.
- `agents/gpu-router.ts`: node discovery, VRAM/load check, circuit breaker after 3 failures, automatic requeue.
- `monitoring/otel-federation.yaml`: batching, 20% sampling, memory limiter, secondary export retry.
- DB patches: `s3_synced`, `assigned_node`.
- Gateway patch: route jobs through GPU router.
- `.env` cluster additions.

Network target:
`Client -> Gateway :3000 -> GPU Router :3003 -> Node(s) :8081 -> S3 Primary/Secondary + OTel/Prometheus/Grafana`

Next target phase:
v2.5.0 Public API & Marketplace: OpenAPI spec, TS/Python SDK clients, credit system, public gallery, partner webhooks.

Verification on 2026-05-01 01:06:17 -04:00:
Missing in canonical folder:
- `deploy/docker-compose.cluster.yml`
- `agents/cluster-sync.ts`
- `agents/gpu-router.ts`
- `monitoring/otel-federation.yaml`
- `gateway/api-gateway.ts`
- `db/schema.sql`

Do not claim v2.4.0 multi-node federation is active until these files are implemented and tested.

## Qwen Full Injector Review - 2026-05-01 01:15:23 -04:00

- User provided EchoesEngine_Full_Injector.ps1 as Qwen 3.6 output.
- It was reviewed but NOT executed because it deletes the target folder and creates a parallel scaffold.
- Created QWEN_FULL_INJECTOR_REVIEW.md documenting useful concepts and integration risks.
- Safe path: extract concepts gradually into the canonical folder; do not replace current working daemon/server/build files.

## v2.4.0 Prep - Dormant DB Schema - 2026-05-01 03:39:42 -04:00

- Added db/schema.sql as dormant PostgreSQL prep for future users/jobs/swarm node state.
- Status: NOT ACTIVE. It does not affect the current C++ daemon, local jobs, renders, or scheduled task.
- Activation requires explicit Docker/Postgres setup and agent/gateway wiring.
