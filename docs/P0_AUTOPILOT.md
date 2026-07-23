# Echoes Cinema P0 Autopilot

Status: **REAL in source/self-test/Windows control-center CI; target-PC AI render still MISSING**

## Purpose

P0 Autopilot removes manual babysitting from the first real AI-video proof.
The browser control center starts first and remains reachable while the provider
repairs its Python environment, downloads the pinned model, and loads it on CUDA.

Once authenticated provider health reports `realModelLoaded=true`, P0 Autopilot:

1. detects an already complete proof and refuses to render a duplicate;
2. preserves incomplete render state for evidence-validated resume;
3. creates the deterministic four-second proof audio on the Cinema workspace;
4. runs `cinema_job_runner.py` with `--resume`;
5. requires `job-result.json status=PASS` and `backendStatus=REAL`;
6. requires `video-qc.json status=PASS`;
7. requires a non-empty final MP4 and SHA-256 artifact evidence;
8. creates `latest-p0-evidence.zip` with secret redaction;
9. publishes the exact phase and blocker in the localhost dashboard.

## Runtime flow

```text
START_ECHOES_CINEMA.cmd
  -> localhost dashboard becomes reachable
  -> provider worker validates/repairs CUDA + Diffusers environment
  -> provider downloads/loads the pinned low-VRAM proof model
  -> P0 Autopilot waits without blocking localhost
  -> realModelLoaded=true
  -> resumable four-second proof render
  -> audio assembly + FFprobe QC + SHA-256
  -> evidence ZIP
  -> dashboard P0 status REAL or BROKEN with exact blocker
```

## Truth states

```text
DORMANT  autorun disabled or mock-contract provider mode
PARTIAL  waiting for provider or rendering
REAL     model load + real backend + final MP4 + QC + hashes all passed
BROKEN   stopped safely; exact blocker and resumable evidence preserved
MISSING  no status exists yet
```

A CI mock provider never enables P0 autorun and can never become the proof used to
claim a real AI clip.

## Persistent files on D:

```text
D:\A.I\EchoesCinema\runtime\p0-autopilot-status.json
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\job-result.json
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\video-qc.json
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\provider-health.json
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\autopilot.log
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\autopilot-error.log
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\run-failure.txt
D:\A.I\EchoesCinema\proofs\first-real-ai-clip\echoes-first-real-ai-clip.mp4
D:\A.I\EchoesCinema\proofs\evidence\latest-p0-evidence.zip
```

Old failure evidence is moved under `proofs\first-real-ai-clip\attempts` before a
new resumable attempt. Models, the Python environment, incomplete clips, and proof
files are not deleted.

## Security boundary

- Provider tokens remain in child-process environment memory.
- Tokens are not placed in process command arguments.
- Autopilot status files explicitly report `secretsPersisted=false`.
- Evidence bundling redacts bearer tokens and known token fields.
- Model/cache/temp/output paths remain on the non-system Cinema workspace.
- Commercial use remains blocked for the local ModelScope proof provider.

## Operational controls

Autorun is enabled by default for `providerMode=real`.

```text
ECHOES_CINEMA_P0_AUTORUN=0
```

disables it without changing code. Mock-contract mode disables it automatically.

Optional timeouts:

```text
ECHOES_CINEMA_P0_WAIT_TIMEOUT=7200
ECHOES_CINEMA_P0_PROVIDER_TIMEOUT=3600
```

## Verified boundary

The control-center contract and Windows one-click smoke verify that:

- the dashboard stays reachable;
- P0 Autopilot is loaded and visible;
- its Python code compiles and self-tests;
- proof idempotency, audio creation, resume requirement, and secret absence pass;
- mock-contract mode keeps P0 `DORMANT`;
- the provider worker and localhost stack start and stop correctly.

The first target-PC model-generated MP4 remains **MISSING** until the HP Omen run
produces `realModelLoaded=true`, `backendStatus=REAL`, QC PASS, SHA-256 evidence,
and visual approval.
