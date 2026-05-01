# CODEX PHASE 4 GUIDE - Public API, LoRA Live, Multi-Node Prep

## Safety

- Do not modify `main.cpp`, `CMakeLists.txt`, CMake presets, or built binaries for Phase 4.
- Keep the C++ daemon as the authoritative runtime.
- Public API must bind to `127.0.0.1` only. Do not expose `0.0.0.0` without a reverse proxy, TLS, and real auth.
- LoRA hot-swap is manual signaling only. No automatic training.
- All modules are dormant on import and direct run. Activation is explicit.

## Added Modules

- `agents/rate-limiter.ts`: in-memory token bucket, Redis-ready later.
- `gateway/public-api.ts`: localhost-only facade with `/v1/health` and `/v1/generate`.
- `agents/lora-manager.ts`: scans `models/lora`, checks dataset readiness, manually signals `:8081/reload_lora`.
- `agents/node-discovery.ts`: health aggregation for GPU workers.
- `scripts/activate-phase4.ps1`: dry-run by default, starts a localhost-only runner only with `-Activate`.

## Validation

```powershell
pnpm exec tsx agents/rate-limiter.ts
pnpm exec tsx agents/lora-manager.ts
pnpm exec tsx agents/node-discovery.ts
pnpm exec tsx gateway/public-api.ts
.\scripts\activate-phase4.ps1 -DryRun
```

## Activation

Only after previous phases are healthy:

```powershell
.\scripts\activate-phase4.ps1 -Activate
Invoke-WebRequest http://127.0.0.1:4000/v1/health -UseBasicParsing
```

## Production Path

1. Put Caddy/Nginx/Cloudflare Tunnel in front of `127.0.0.1:4000`.
2. Replace fallback API key handling with DB/Redis-backed keys.
3. Move token buckets to Redis for multi-node state.
4. Add an internal DNS list for GPU workers.
5. Keep LoRA training offline; only hot-swap known-good `.safetensors` artifacts.
