#!/usr/bin/env python3
"""Local-only browser control center for the durable Echoes Cinema service.

The protected job API remains authenticated. Only the dashboard HTML and its
read-only summary endpoint are public, and only when both the server binding and
the connecting client are loopback addresses. This prevents a browser opened on
localhost from showing a misleading connection/authentication error while keeping
remote bindings fail-closed.
"""

from __future__ import annotations

import html
import ipaddress
import json
import urllib.parse
from pathlib import Path
from typing import Any

import cinema_job_service as base
import cinema_job_service_durable as durable

DASHBOARD_SCHEMA = "echoes.cinema-control-center.v1"
PUBLIC_PATHS = frozenset({"/", "/index.html", "/v1/control-center/status"})

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Echoes Cinema Control Center</title>
<style>
:root { color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; }
body { margin: 0; background: #090b0f; color: #f4f6fa; }
main { max-width: 1120px; margin: 0 auto; padding: 28px 20px 48px; }
h1 { margin: 0; font-size: clamp(28px, 5vw, 48px); letter-spacing: .04em; }
.subtitle { color: #aeb7c7; margin: 8px 0 28px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(230px,1fr)); gap: 14px; }
.card { background: #121722; border: 1px solid #263044; border-radius: 14px; padding: 18px; min-height: 116px; box-shadow: 0 12px 32px rgba(0,0,0,.22); }
.label { color: #8f9bb0; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }
.value { font-size: 24px; margin-top: 10px; font-weight: 700; overflow-wrap: anywhere; }
.detail { color: #b9c2d0; font-size: 13px; margin-top: 8px; line-height: 1.45; overflow-wrap: anywhere; }
.badge { display: inline-block; border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 800; letter-spacing: .08em; }
.PASS,.REAL { background:#113d2a; color:#72f2b2; }
.PARTIAL,.MISSING { background:#49390d; color:#ffd978; }
.FAILED,.BROKEN { background:#501a20; color:#ff98a4; }
.QUEUED,.RUNNING,.RECOVERABLE { background:#153c5a; color:#91d8ff; }
section { margin-top: 18px; }
pre { white-space: pre-wrap; word-break: break-word; background:#0c1018; border:1px solid #222b3c; border-radius:12px; padding:16px; color:#c8d0dc; max-height:360px; overflow:auto; }
footer { color:#778399; margin-top:24px; font-size:12px; }
</style>
</head>
<body>
<main>
<h1>ECHOES CINEMA</h1>
<p class="subtitle">Local control center — this page stays available while the AI model loads.</p>
<div class="grid">
  <div class="card"><div class="label">Stack</div><div id="stack" class="value">Connecting…</div><div id="stackDetail" class="detail"></div></div>
  <div class="card"><div class="label">AI provider</div><div id="provider" class="value">Checking…</div><div id="providerDetail" class="detail"></div></div>
  <div class="card"><div class="label">Real jobs</div><div id="realJobs" class="value">—</div><div id="commercialJobs" class="detail"></div></div>
  <div class="card"><div class="label">Queue</div><div id="queue" class="value">—</div><div id="workers" class="detail"></div></div>
</div>
<section class="card">
  <div class="label">Exact next action / blocker</div>
  <div id="nextAction" class="value">Loading status…</div>
  <div id="error" class="detail"></div>
</section>
<section class="card">
  <div class="label">Recent jobs</div>
  <pre id="jobs">No jobs yet.</pre>
</section>
<footer>Refreshes automatically every 2 seconds. Protected render APIs still require authentication.</footer>
</main>
<script>
const byId = id => document.getElementById(id);
function badge(value) { return `<span class="badge ${value}">${value}</span>`; }
function text(value, fallback='—') { return value === undefined || value === null || value === '' ? fallback : String(value); }
async function refresh() {
  try {
    const response = await fetch('/v1/control-center/status', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    byId('stack').innerHTML = badge(data.status || 'PARTIAL');
    byId('stackDetail').textContent = `Service ${text(data.service?.backend)} · port ${text(data.service?.port)}`;
    const p = data.provider || {};
    byId('provider').innerHTML = badge(p.status || 'PARTIAL');
    byId('providerDetail').textContent = `${text(p.modelId, 'model not loaded')} · ${text(p.gpuName, 'GPU pending')}`;
    byId('realJobs').textContent = data.acceptingRealJobs ? 'READY' : 'NOT READY';
    byId('commercialJobs').textContent = `Commercial: ${data.acceptingCommercialJobs ? 'READY' : 'NOT READY'}`;
    const s = data.scheduler || {};
    byId('queue').textContent = `${text(s.queuedCount, 0)} queued`;
    byId('workers').textContent = `${text(s.runningCount, 0)} running · max ${text(s.maxWorkers, 1)}`;
    byId('nextAction').textContent = text(data.nextAction, 'Inspect service logs.');
    byId('error').textContent = text(data.error, '');
    byId('jobs').textContent = JSON.stringify(data.jobs || [], null, 2);
  } catch (error) {
    byId('stack').innerHTML = badge('BROKEN');
    byId('stackDetail').textContent = 'The local service stopped responding.';
    byId('nextAction').textContent = 'Run START_ECHOES_CINEMA.cmd again. The supervisor will repair or restart the stack.';
    byId('error').textContent = String(error);
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def is_loopback(value: str) -> bool:
    normalized = value.strip().strip("[]")
    if normalized.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def public_dashboard_allowed(handler: "ControlCenterHandler") -> bool:
    return is_loopback(handler.cinema.config.host) and is_loopback(handler.client_address[0])


def scheduler_counts(snapshot: dict[str, Any]) -> dict[str, Any]:
    queued = snapshot.get("queued")
    running = snapshot.get("running")
    return {
        **snapshot,
        "queuedCount": len(queued) if isinstance(queued, list) else int(snapshot.get("queuedCount", 0) or 0),
        "runningCount": len(running) if isinstance(running, list) else int(snapshot.get("runningCount", 0) or 0),
    }


def build_status(server: base.CinemaServer) -> dict[str, Any]:
    registry = server.registry
    scheduler: dict[str, Any] = {}
    jobs: list[dict[str, Any]] = []
    if isinstance(registry, durable.DurableJobRegistry):
        scheduler = scheduler_counts(registry.scheduler.snapshot())
        jobs = registry.ledger.list_jobs()
    else:
        with registry.lock:
            jobs = [dict(value) for value in registry.jobs.values()]
        scheduler = {
            "queuedCount": sum(1 for job in jobs if job.get("status") == "QUEUED"),
            "runningCount": sum(1 for job in jobs if job.get("status") == "RUNNING"),
            "maxWorkers": server.config.max_workers,
        }

    provider_summary: dict[str, Any]
    accepting_real = False
    accepting_commercial = False
    provider_error = ""
    try:
        provider = base.fetch_provider_health(
            server.config.provider_endpoint,
            server.config.provider_token,
            min(server.config.provider_timeout, 5.0),
        )
        accepting_real = base.accepting_real_jobs(provider)
        accepting_commercial = base.accepting_commercial_jobs(provider)
        provider_summary = {
            "status": "PASS" if provider.get("realModelLoaded") is True else "PARTIAL",
            "realModelLoaded": provider.get("realModelLoaded") is True,
            "modelId": provider.get("modelId"),
            "modelRevision": provider.get("modelRevision"),
            "commercialUseAllowed": provider.get("commercialUseAllowed") is True,
            "license": provider.get("license"),
            "gpuName": (provider.get("gpu") or {}).get("name") if isinstance(provider.get("gpu"), dict) else None,
            "loadError": provider.get("loadError"),
        }
        if provider.get("loadError"):
            provider_error = str(provider.get("loadError"))
    except Exception as error:  # noqa: BLE001 - dashboard must show the exact local blocker
        provider_error = str(error)
        provider_summary = {
            "status": "PARTIAL",
            "realModelLoaded": False,
            "modelId": None,
            "commercialUseAllowed": False,
            "loadError": provider_error,
        }

    if accepting_real:
        next_action = "The real provider is ready. Submit or resume a Cinema job."
        status = "PASS"
    elif provider_error:
        next_action = "The control center is online. The AI provider is still loading or blocked; inspect the exact message below."
        status = "PARTIAL"
    else:
        next_action = "The control center is online and waiting for the AI provider to finish loading."
        status = "PARTIAL"

    return {
        "schema": DASHBOARD_SCHEMA,
        "status": status,
        "service": {
            "backend": "cinema-durable-control-center",
            "host": server.config.host,
            "port": server.config.port,
            "manifestGenerator": "native-render-manifest-cli" if server.config.manifest_cli else "python-render-manifest-v1",
        },
        "provider": provider_summary,
        "acceptingRealJobs": accepting_real,
        "acceptingCommercialJobs": accepting_commercial,
        "scheduler": scheduler,
        "jobs": jobs[-20:],
        "nextAction": next_action,
        "error": provider_error or None,
    }


class ControlCenterHandler(durable.DurableHandler):
    server_version = "EchoesCinemaControlCenter/1.0"

    def send_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in PUBLIC_PATHS:
            if not public_dashboard_allowed(self):
                self.send_json(403, {"status": "FAILED", "error": "public dashboard is loopback-only"})
                return
            if path in {"/", "/index.html"}:
                self.send_html(200, DASHBOARD_HTML)
            else:
                self.send_json(200, build_status(self.cinema))
            return
        super().do_GET()


def self_test() -> int:
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("localhost")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("192.168.1.10")
    assert "/v1/control-center/status" in DASHBOARD_HTML
    assert "Run START_ECHOES_CINEMA.cmd again" in DASHBOARD_HTML
    assert "Protected render APIs still require authentication" in DASHBOARD_HTML
    assert "localhost refused" not in DASHBOARD_HTML.lower()
    print("CinemaControlCenter PASS loopback=protected dashboard=embedded polling=enabled")
    return 0


def main() -> int:
    args = base.parse_args()
    if getattr(args, "self_test", False):
        return self_test()
    config = base.ServiceConfig.from_args(args)
    server = base.CinemaServer((config.host, config.port), ControlCenterHandler)
    server.config = config
    server.registry = durable.DurableJobRegistry(config)
    print(
        f"EchoesCinemaControlCenter READY http://{config.host}:{config.port} "
        f"ledger={server.registry.ledger.config.path} outputRoot={config.output_root} "
        f"maxWorkers={config.max_workers}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.registry.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
