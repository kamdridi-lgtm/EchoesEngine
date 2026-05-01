# Qwen Full Injector Review

Reviewed: 2026-05-01 01:15:23 -04:00
Source: user-pasted EchoesEngine_Full_Injector.ps1

## Decision
Do not execute this script directly against the canonical EchoesEngine folder.

Reason: the script starts by deleting the target folder:

`powershell
if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
`

It also creates a new scaffold project (EchoesEngine_Full) instead of applying targeted patches to the canonical working folder.

Canonical folder remains:
C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine

## Useful Concepts To Extract

- db/schema.sql: users, jobs, swarm_nodes tables, including s3_synced and ssigned_node fields.
- docker-compose.yml: local Postgres + Redis stack.
- gents/gateway/api-gateway.ts concept: public API wrapper in front of the C++ core.
- gents/adaptive-orchestrator.ts: 24/7 auto-submitter/monitor loop concept.
- gents/swarm-coordinator.ts: node registry and load tracking concept.
- gents/video-diffusion-worker.py: future GPU diffusion worker on port 8081.
- gents/post-processor.py: future FFmpeg post-processing worker on port 8082.
- monitoring configs and dashboard concept.
- CODEX_CONTEXT.md / RUN_INSTRUCTIONS.md: useful documentation shape.

## Problems Found

- Destructive bootstrap: deletes target folder before writing.
- Creates a parallel project rather than patching current architecture.
- C++ snippets use invalid combined include syntax such as #include <queue> <mutex>.
- main.cpp.patch is only a comment/instruction, not an applied patch.
- TypeScript gateway uses eq.user without Fastify type augmentation and no persistent user source.
- Python LoRA script contains invalid inline if after a semicolon in normal Python syntax.
- Several files are placeholders and would not be production-ready without dependencies, auth, env validation, and integration tests.
- The script assumes ports/components that are not installed yet in the canonical runtime.

## Safe Integration Plan

1. Keep current working daemon/server intact.
2. Add missing directories only when needed: gents, gateway, db, deploy, monitoring, public.
3. First integrate documentation and environment/schema templates.
4. Then add gateway/router/cluster code behind disabled feature flags.
5. Only after successful local tests, enable one component at a time.
6. Never replace current CMakeLists.txt, CMakePresets.json, or main.cpp with this scaffold.

## Status

Stored as reference only. Not installed. Not active.
