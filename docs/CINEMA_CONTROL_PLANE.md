# Echoes Cinema Control Plane

Status: **PARTIAL / ALL CONTROL CONTRACTS IMPLEMENTED; REAL MODEL OUTPUT STILL UNPROVEN**

## Canonical runtime

```text
K-Core :3000
  -> cinema.health
  -> cinema.status
  -> cinema.submit (kill switch protected)

Echoes Cinema Job Service :8090
  -> authenticates K-Core
  -> validates job id and relative asset paths
  -> checks provider health
  -> refuses jobs unless realModelLoaded=true
  -> launches cinema_job_runner.py without a shell

Diffusers Video Provider :8081
  -> authenticates the job worker
  -> loads one real Diffusers pipeline
  -> GET /health
  -> POST /v1/render
  -> returns H.264/yuv420p MP4 clips

Cinema Job Runner
  -> RenderManifestCli
  -> authenticated provider requests
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
8090  Echoes Cinema job service
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
3. Start the Cinema job service.
4. Start K-Core with the matching service and provider URLs/tokens.
5. Enable `KCORE_MUTATIONS_ENABLED=true` only when real job submission is wanted.

Provider:

```powershell
.\scripts\start-diffusers-provider.ps1 `
  -ModelId "<model-id-or-local-path>" `
  -Token $providerToken `
  -Device cuda
```

Job service:

```powershell
.\scripts\start-cinema-job-service.ps1 `
  -ServiceToken $serviceToken `
  -ProviderToken $providerToken `
  -ManifestCli ".\build-prompt\Release\RenderManifestCli.exe" `
  -SectionsRoot ".\cinema-input\sections" `
  -AudioRoot ".\cinema-input\audio" `
  -OutputRoot ".\cinema-jobs"
```

K-Core environment:

```text
KCORE_MUTATIONS_ENABLED=true
ECHOES_CINEMA_HEALTH_URL=http://127.0.0.1:8081/health
ECHOES_CINEMA_PROVIDER_TOKEN=<provider token>
ECHOES_CINEMA_SERVICE_URL=http://127.0.0.1:8090
ECHOES_CINEMA_SERVICE_TOKEN=<service token>
```

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
    "seed": 7331
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
- HTTP 200 is not proof of a successful render.
- Every clip and the final MP4 must pass FFprobe QC.
- K-Core audit events use `COMPLETED`, not fake `SUCCESS`.

## Current proof boundary

Implemented and contract-tested:

```text
provider fail-closed health
job service fail-closed submission
K-Core health/status/submit transport
authentication
path validation
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
