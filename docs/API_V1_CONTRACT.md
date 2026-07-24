# EchoesEngine API v1 Contract

This document defines the canonical API boundary shared by K-Core, EchoesStudio, DreamGirl, and other clients.

## Principles

- Browser clients never receive engine secrets.
- Audio processing and video generation are distinct job types.
- Every mutation is authenticated, idempotent, and auditable.
- Native executable paths are never returned to clients.
- Statuses are restricted to `QUEUED`, `PROCESSING`, `QC`, `FINISHED`, and `FAILED`.

## Endpoints

### `GET /v1/health`

Returns engine identity and capability state.

```json
{
  "service": "echoes-engine",
  "version": "0.5.0",
  "status": "REAL",
  "capabilities": {
    "audioProcessing": "REAL",
    "videoGeneration": "MISSING",
    "onnx": "PARTIAL",
    "opencl": "OPTIONAL"
  }
}
```

### `POST /v1/jobs`

Required headers:

- `Authorization: Bearer <server-to-server token>`
- `Idempotency-Key: <unique request id>`
- `Content-Type: application/json`

Audio request:

```json
{
  "type": "audio.process",
  "input": {
    "uri": "private-object-reference",
    "sha256": "hex",
    "mimeType": "audio/wav"
  },
  "preset": "mastering-safe",
  "options": {
    "stems": ["dry", "processed", "spatial", "full"]
  }
}
```

Response:

```json
{
  "jobId": "JOB_...",
  "status": "QUEUED",
  "createdAt": "ISO-8601"
}
```

### `GET /v1/jobs/:jobId`

```json
{
  "jobId": "JOB_...",
  "type": "audio.process",
  "status": "QC",
  "progress": 90,
  "engine": "EchoesEngine C++",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "qc": null,
  "error": null
}
```

### `GET /v1/results/:jobId`

Only available for `FINISHED` jobs and authorized owners/services.

```json
{
  "jobId": "JOB_...",
  "status": "FINISHED",
  "outputs": [
    {
      "kind": "processed",
      "uri": "short-lived-signed-reference",
      "sha256": "hex",
      "mimeType": "audio/wav"
    }
  ],
  "qc": {
    "status": "PASS",
    "peakDb": -1.0,
    "rmsDb": -14.0,
    "clipping": false,
    "silence": false
  }
}
```

## Error envelope

```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "Human-readable summary",
    "retryable": false
  }
}
```

## Required server behavior

- Validate MIME signature, size, duration, and hash.
- Generate server-side job IDs and output paths.
- Enforce per-job timeout and process termination.
- Record all state transitions.
- Never mark a job `FINISHED` before QC completes.
- Return `FAILED` with the exact sanitized failure category.
