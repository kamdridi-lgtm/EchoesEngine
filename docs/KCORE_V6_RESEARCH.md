# K-CORE / v6 Archive Review

Status: research only. Do not activate or import directly into the current EchoesEngine daemon.

Reviewed sources:

- `C:\Users\Administrator\OneDrive\Bureau\k-core-operational-control-merged-local-fresh-1776417777 (1).zip`
- `C:\Users\Administrator\OneDrive\Bureau\v6`

## What K-CORE Contains

K-CORE is a local operational control layer built with Express, Vite, React, and TypeScript. It watches a real local EchoesEngine folder through `ECHOESENGINE_PATH`, writes JSON state files, and exposes a dashboard/API.

Useful concepts to keep:

- Truth labels: `REAL`, `PRESENTUNTESTED`, `SIMULATED`, `PLACEHOLDER`, `FUTURE_WIRING`, `BROKEN`, `UNKNOWN`.
- State files: `system_state`, `engine_state`, `repo_state`, `logs_index`, `alerts`, `mission_state`, `release_state`, `asset_state`, `store_state`.
- Backend refresh loop concept: probe repo, detect binary/build state, parse logs, emit alerts.
- UI pattern: dashboard that clearly separates real state from future wiring.

Do not copy as-is yet:

- `server.ts` starts a polling server automatically.
- UI polling is fine for a dashboard, but not for dormant prep modules.
- Binary search paths are old and do not match the current canonical build output exactly.

## What v6 Contains

`v6` is a KAMDRIDI revenue/ops AWS SAM system, not the current local EchoesEngine runtime. It includes:

- AWS SAM template with API Gateway, DynamoDB, Lambda agents, Step Functions, CloudWatch, SES/SNS, S3, Secrets Manager.
- Lambda agents:
  - `master_orchestrator`
  - `crm_agent`
  - `revenue_agent`
  - `email_agent`
  - `analytics_agent`
  - `watchdog_agent`
  - `event_receiver`
  - `admin_api`
- DynamoDB tables:
  - leads
  - events / Stripe dedup ledger
  - customers
  - orders
  - licenses
  - subscriptions
  - business events
  - ops alerts
- Step Functions:
  - lead pipeline
  - messaging follow-up flow
  - ops recovery flow
  - admin projection refresh flow

Useful concepts to keep:

- Canonical status constants instead of scattered strings.
- Event ledger / dedup pattern with TTL.
- Append-only business event log.
- Stripe checkout idempotency key pattern.
- Stripe webhook signature validation and replay protection.
- Lead/customer/order/license status lifecycle.
- Watchdog alert lifecycle: open, acknowledged, resolved.
- Dry-run email mode before going live.
- Admin API pattern for manual correction and recovery.

Do not deploy from this folder directly:

- It is a separate AWS revenue system.
- It requires real AWS/SAM/Secrets/DynamoDB/SES setup.
- It is not wired to the current local C++ daemon.
- Some files have encoding artifacts from copied text.

## EchoesEngine Reuse Plan

Near-term, safe dormant additions:

1. Add a dormant `status-catalog.ts` for canonical EchoesEngine job/runtime statuses.
2. Add a dormant `state-labels.ts` to reuse the K-CORE truth label discipline.
3. Add a dormant `event-ledger.ts` interface for local append-only event records, no I/O unless explicitly called.
4. Later, adapt dashboard ideas into an EchoesEngine local dashboard without changing the daemon.

What we already have that maps to this review:

- `agents/qc-guard.ts`: validates renders.
- `agents/auto-curator.ts`: queues QC-passed renders for future LoRA dataset JSONL.
- `agents/event-dispatcher.ts`: formats lifecycle events.
- `agents/notification-bridge.ts`: formats Discord/WhatsApp notifications.
- `agents/telemetry-bridge.ts`: in-memory metrics/traces.
- `gateway/api-gateway.ts`: dormant future routing facade.

## Safety Rule

Treat these archives as reference architecture, not drop-in code. The active runtime remains:

```text
Windows Task Scheduler -> scripts/daemon.bat -> C++ EchoesEngine daemon -> 127.0.0.1:8080
```

No AWS deployment, no Firebase deployment, no webhook activation, and no polling service should be started unless explicitly requested.
