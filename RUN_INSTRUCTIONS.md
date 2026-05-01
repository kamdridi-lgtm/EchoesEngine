# RUN INSTRUCTIONS - EchoesEngine

## Active Local Runtime

The active production-like local runtime is the C++ daemon managed by Windows Task Scheduler.

Check status:

```powershell
Get-ScheduledTask -TaskName EchoesEngineDaemon | Format-List TaskName,State
cmd.exe /c "netstat -ano | findstr :8080"
```

The daemon scripts live in:

```text
scripts\build.bat
scripts\start-server.bat
scripts\daemon.bat
scripts\install-daemon-task.ps1
scripts\autojob-agent.ps1
```

## Dormant Agent Smoke Tests

These commands should initialize and exit. They must not submit jobs.

```powershell
cd C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine
pnpm dlx tsx agents/adaptive-orchestrator.ts
pnpm dlx tsx agents/swarm-coordinator.ts
```

Expected output:

```text
[AdaptiveOrchestrator] Initialized (dormant mode)
[SwarmCoordinator] Initialized (dormant mode)
```

## Dormant Database Stack

The local Postgres/Redis stack is not active by default. Start only when explicitly needed:

```powershell
docker compose up -d
```

Stop it:

```powershell
docker compose down
```

## Important

Do not run old bootstrap scripts that remove/recreate folders. Add v2.4 pieces gradually and keep them dormant until verified.
