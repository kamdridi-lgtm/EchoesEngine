# Echoes Cinema Control Plane

Status: **PARTIAL / CONTROL, DURABILITY, PRIORITY, AND STORAGE CONTRACTS IMPLEMENTED; REAL MODEL OUTPUT STILL UNPROVEN**

## Canonical runtime

```text
K-Core :3000
  -> cinema.health
  -> cinema.status
  -> cinema.submit (kill switch protected)

Echoes Cinema Durable Job Service :8090
  -> authenticates K-Core
  -> validates job id and relative asset paths
  -> checks provider health and commercial-use requirements
  -> rejects work unless realModelLoaded=true
  -> persists QUEUED/RUNNING/RECOVERABLE/terminal truth states
  -> orders queued jobs by priority, then FIFO sequence
  -> bounds simultaneous jobs with maxWorkers
  -> reserves D-drive capacity before accepting work
  -> launches cinema_job_runner.py without a shell

Diffusers Video Provider :8081
  -> authenticates the job worker
  -> loads one real Diffusers pipeline
  -> GET /health
  -> POST /v1/render
  -> returns H.264/yuv420p MP4 clips

Cinema Job Runner
  -> compiler-free Python manifest generator or optional RenderManifestCli
  -> authenticated provider requests
  -> evidence-validated per-clip resume
  -> per-clip FFprobe QC
  -> final assembly
  -> optional audio mux
  -> job-result.json
```

## Ports

```text
3000  K-Core operational control
4000  EchoesEngine public API / health when enabled
8081  local Diffusers render provider
8090  Echoes Cinema durable job service
```

## Tokens

Use three different random secrets:

```text
KCORE_ADMIN_TOKEN
ECHOES_CINEMA_SERVICE_TOKEN
ECHOES_RENDER_TOKEN
```

K-Core uses `ECHOES_CINEMA_SERVICE_TOKEN` to submit and inspect jobs. The
Cinema job service uses `ECHOES_RENDER_TOKEN` to access the provider. Tokens are
read from environment variables and are not written into render state or job
results.

## Start order on Windows

1. Start the real Diffusers provider.
2. Confirm `realModelLoaded=true` through authenticated `/health`.
3. Start the durable Cinema job service.
4. Start K-Core with the matching service and provider URLs/tokens.
5. Enable `KCORE_MUTATIONS_ENABLED=true` only when real job submission is wanted.

Provider:

```powershell
.\scripts\start-diffusers-provider.ps1 `
  -ModelId "<model-id-or-local-path>" `
  -Token $providerToken `
  -Device cuda
```

Durable job service with D-drive protection:

```powershell
.\scripts\start-cinema-job-service.ps1 `
  -ServiceToken $serviceToken `
  -ProviderToken $providerToken `
  -SectionsRoot "D:\A.I\EchoesCinema\cinema-input\sections" `
  -AudioRoot "D:\A.I\EchoesCinema\cinema-input\audio" `
  -OutputRoot "D:\A.I\EchoesCinema\jobs" `
  -MaxWorkers 1 `
  -StorageReserveGiB 20 `
  -DefaultJobGiB 8 `
  -MaxJobGiB 200
```

`MaxWorkers=1` is the safe local default for a single RTX GPU. Jobs can still be
submitted in advance: the scheduler keeps them ordered without running multiple
GPU renders simultaneously.

K-Core environment:

```text
KCORE_MUTATIONS_ENABLED=true
ECHOES_CINEMA_HEALTH_URL=http://127.0.0.1:8081/health
ECHOES_CINEMA_PROVIDER_TOKEN=<provider token>
ECHOES_CINEMA_SERVICE_URL=http://127.0.0.1:8090
ECHOES_CINEMA_SERVICE_TOKEN=<service token>
```

## Job request controls

`priority` must be an integer from `0` to `100`; `100` runs first. Equal
priorities remain FIFO. `estimatedOutputBytes` is a conservative reservation
held while the job is queued or running. Admission fails before execution when
free D-drive storage minus active reservations would cross the configured
reserve.

```json
{
  "schema": "echoes.cinema-job-request.v1",
  "jobId": "echoes-first-real-clip",
  "sectionsCsv": "first-real-clip.csv",
  "audioFile": "first-real-clip.wav",
  "seed": 7331,
  "priority": 90,
  "estimatedOutputBytes": 8589934592,
  "commercialUseRequired": false
}
```

Interrupted work is never silently restarted. A recovered job reports
`RECOVERABLE` and must be resubmitted explicitly:

```json
{
  "schema": "echoes.cinema-job-request.v1",
  "jobId": "echoes-first-real-clip",
  "sectionsCsv": "first-real-clip.csv",
  "audioFile": "first-real-clip.wav",
  "seed": 7331,
  "priority": 90,
  "estimatedOutputBytes": 8589934592,
  "resumeRecovered": true
}
```

The resumed runner reuses only clips whose SHA-256 and H.264/yuv420p media QC
remain valid and whose provider identity, model revision, licence, and required
capabilities still match.

## Service inspection endpoints

Authenticated service endpoints:

```text
GET /health
GET /v1/cinema/jobs
GET /v1/cinema/jobs/<jobId>
GET /v1/cinema/scheduler
POST /v1/cinema/jobs
```

`GET /v1/cinema/scheduler` reports current running and queued jobs, priority
order, worker limit, recent worker failures, actual free bytes, reserved bytes,
and projected free bytes.

## K-Core actions

Provider truth:

```json
{
  "action": "cinema.health"
}
```

Submit a job:

```json
{
  "action": "cinema.submit",
  "idempotencyKey": "echoes-first-real-clip-001",
  "params": {
    "jobId": "echoes-first-real-clip",
    "sectionsCsv": "first-real-clip.csv",
    "audioFile": "first-real-clip.wav",
    "seed": 7331,
    "priority": 90,
    "estimatedOutputBytes": 8589934592
  }
}
```

Read job status:

```json
{
  "action": "cinema.status",
  "params": {
    "jobId": "echoes-first-real-clip"
  }
}
```

## Security and truth rules

- No user request may supply an executable path or shell command.
- Sections and audio files must be relative paths inside configured roots.
- The provider and job service require bearer authentication.
- K-Core mutations remain disabled by default.
- A reachable provider is not automatically REAL.
- `realModelLoaded=true` is mandatory before a real job is accepted.
- Commercial work must pass the explicit provider licence gate.
- Storage admission is conservative and fails closed.
- HTTP 200 is not proof of a successful render.
- Every reused or generated clip and the final MP4 must pass evidence/QC checks.
- K-Core audit events use `COMPLETED`, not fake `SUCCESS`.

## Current proof boundary

Implemented and contract-tested:

```text
provider fail-closed health
job service fail-closed submission
authentication and path validation
durable atomic job ledger
restart recovery as RECOVERABLE
explicit evidence-validated resume
priority queue with FIFO tie-breaking
bounded worker concurrency
D-drive storage reservation and emergency reserve
scheduler inspection endpoint
K-Core health/status/submit transport
mutation kill switch
manifest execution
MP4 assembly and QC
```

Still required before classification as REAL AI VIDEO:

```text
real model loaded on the target GPU
first model-generated 3-5 second clip
provider health realModelLoaded=true
job-result.json backendStatus=REAL
visual review confirming the output is not a synthetic fixture
```
