# Canonical Status Schema

## Component truth status

Allowed values:

- `REAL`: implemented and verified by a reproducible test.
- `PARTIAL`: meaningful implementation exists but the full contract is not verified.
- `MOCK`: deterministic substitute used for development; never customer-deliverable.
- `DORMANT`: code is present but intentionally disabled.
- `BROKEN`: expected behavior fails.
- `MISSING`: no meaningful implementation exists.

## Job execution status

Allowed values:

- `QUEUED`
- `PROCESSING`
- `QC`
- `FINISHED`
- `FAILED`

## Invariants

1. A component cannot be both `REAL` and `DORMANT`.
2. An initialized module is not automatically `REAL`.
3. `FINISHED` requires a persisted output and completed QC.
4. A mock output must carry `componentStatus: MOCK`.
5. A failed native process must produce `FAILED`, never a fallback success unless the fallback is explicit in the request and result.
6. Reports must identify the exact route, environment, engine, and test input used.
