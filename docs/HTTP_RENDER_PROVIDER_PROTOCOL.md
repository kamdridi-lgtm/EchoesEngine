# Echoes HTTP Render Provider Protocol

Status: **PARTIAL / IMPLEMENTED AND CONTRACT-TESTED**

This protocol lets Echoes Cinema send one render task at a time to an external
video provider without giving that provider shell access to EchoesEngine.

## Request

`POST /v1/render`

Headers:

```text
Authorization: Bearer <provider token>
Content-Type: application/json
Accept: video/mp4, application/json
```

Body:

```json
{
  "schema": "echoes.render-request.v1",
  "jobId": "echoes-cinema-job-001",
  "task": {
    "id": "echoes-cinema-job-001-task-1",
    "shotId": "intro-shot-1",
    "startSeconds": 0.0,
    "durationSeconds": 4.0,
    "seed": 4242,
    "camera": "slow_push",
    "transition": "fade_in",
    "prompt": "Render ... Preserve subject identity and visual continuity.",
    "outputFile": "clips/intro-shot-1.mp4",
    "subjectId": "kam-dridi",
    "styleId": "echoes-cinematic-hard-rock",
    "referenceAsset": "references/kam-dridi-master.png",
    "continuityStrength": 0.9
  }
}
```

A provider may ignore optional continuity fields only when it declares that
limitation in its health response. It must never claim identity continuity if
it did not use the supplied identity/reference controls.

## Response mode A: direct MP4

```text
HTTP 200
Content-Type: video/mp4
```

The body is the rendered clip.

## Response mode B: result URL

```json
{
  "status": "PASS",
  "outputUrl": "https://allowlisted-provider.example/jobs/123/clip.mp4"
}
```

The worker downloads `outputUrl` only when its host is allowlisted.

## Required clip properties

The current canonical worker accepts a clip only after `ffprobe` verifies:

- one video stream exists;
- codec is H.264;
- pixel format is `yuv420p`;
- duration is positive;
- file size is positive.

A task is `PASS` only after the file exists and QC succeeds. HTTP 200 alone is
not proof of a successful render.

## Security boundaries

`tools/http_render_worker.py`:

- never executes provider-supplied commands;
- rejects absolute paths and `..` traversal in `outputFile`;
- permits plain HTTP only for localhost;
- restricts all provider and download URLs to
  `ECHOES_RENDER_HOST_ALLOWLIST`;
- reads the bearer token from `ECHOES_RENDER_TOKEN` by default;
- does not write the bearer token to render state;
- limits response bodies to 250 MiB;
- writes exact failures into `echoes.render-state.v1`.

## Environment

```text
ECHOES_RENDER_ENDPOINT=https://provider.example/v1/render
ECHOES_RENDER_TOKEN=<secret>
ECHOES_RENDER_HOST_ALLOWLIST=provider.example,cdn.provider.example
```

For a local worker:

```text
ECHOES_RENDER_ENDPOINT=http://127.0.0.1:8081/v1/render
ECHOES_RENDER_HOST_ALLOWLIST=127.0.0.1,localhost
```

## Proof classification

The GitHub contract workflow uses a local fixture that returns real H.264 MP4
files over authenticated HTTP. This proves the network protocol, authentication,
manifest execution, file handling, assembly, and QC.

It **does not** prove that an AI diffusion model is loaded. A provider becomes
`REAL AI VIDEO` only after its health endpoint reports a real model, a task is
rendered from that model, and the resulting clip passes the same QC pipeline.
