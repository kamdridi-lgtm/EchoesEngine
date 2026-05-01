# CODEX PHASE 5 GUIDE - Closed Loop and Revenue Reinvestment

## Safety

- Do not modify `main.cpp`, `CMakeLists.txt`, CMake presets, or binaries for Phase 5.
- Do not hardcode AWS, Stripe, webhook, or API secrets.
- Keep this phase dormant by default.
- `activate-phase5.ps1 -Activate` runs a finite dry-run rehearsal only. It does not start a 24/7 loop.
- Real production mutation requires explicit adapters for DynamoDB, Stripe, SAM ingest, and scaling state.

## Added Modules

- `agents/revenue-reinvestor.ts`: dry-run revenue threshold and upgrade/scale decision model.
- `agents/closed-loop-runner.ts`: single-cycle closed-loop rehearsal.
- `scripts/activate-phase5.ps1`: dry-run by default, finite rehearsal with `-Activate`.

## Validation

```powershell
pnpm exec tsx agents/revenue-reinvestor.ts
pnpm exec tsx agents/closed-loop-runner.ts
.\scripts\activate-phase5.ps1 -DryRun
```

## Rehearsal

```powershell
.\scripts\activate-phase5.ps1 -Activate
```

Expected behavior:

- Initializes `ClosedLoopRunner`.
- Initializes `RevenueReinvestor`.
- Runs three finite dry-run cycles.
- Exits without opening ports or mutating external systems.

## Production Gates

1. SES production access approved and tested.
2. Stripe secrets and webhook secrets stored in Secrets Manager.
3. Admin key and ingest key are active.
4. `PublicBaseUrl` is real.
5. Existing `kamdridi-prod-*` resources are imported, migrated, or renamed deliberately.
6. Redis-backed rate limiting is wired before public traffic.
7. Closed-loop live mode has durable audit logs and a manual kill switch.
