# Execution Backlog

## P0 — Foundation

- [x] Canonical architecture decision
- [x] Shared status vocabulary
- [x] Versioned API contract
- [x] Windows build workflow added
- [ ] Make Windows CI green
- [ ] Publish a reproducible Release artifact
- [ ] Add deterministic WAV fixture
- [ ] Add golden-output regression test
- [ ] Add DSP A/B/C proof test

## P1 — Native engine hardening

- [ ] Refresh README to actual v0.5 architecture
- [ ] Document standalone CLI arguments
- [ ] Add structured native exit codes
- [ ] Add per-stage processing telemetry
- [ ] Add safe mastering preset
- [ ] Add mono-safety analyzer
- [ ] Add dry/processed/spatial/full stem manifest

## P2 — API worker

- [ ] Implement `/v1/health`
- [ ] Implement authenticated `POST /v1/jobs`
- [ ] Implement `GET /v1/jobs/:jobId`
- [ ] Implement authorized result retrieval
- [ ] Add idempotency and state-transition persistence
- [ ] Add timeout, cleanup, and crash recovery

## P3 — K-Core

- [ ] Replace simulated `/api/action`
- [ ] Add read-only command registry
- [ ] Add authentication and roles
- [ ] Add immutable audit log
- [ ] Add kill switch and idempotency keys
- [ ] Enable mutations individually after tests

## P4 — Advanced audio modules

- [ ] QSound/spatial engine
- [ ] Fatigue simulation 2h/4h
- [ ] Micro pitch drift
- [ ] Naturalness scoring
- [ ] ONNX model manager
- [ ] Neural scheduler
- [ ] Multi-state renderer
- [ ] Psychoacoustic engine
