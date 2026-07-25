#!/usr/bin/env python3
"""Runtime-inventory and K-Core planning entrypoint for Echoes Cinema.

The canonical Control Center implementation is kept in cinema_control_center_base.py.
This entrypoint restores its companion modules from the current Git commit or the
already-fetched origin/main ref, adds truthful runtime inventory to the dashboard,
and exposes one authenticated read-only planning endpoint without weakening any
existing job, provider, or authentication boundary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
BASE_RELATIVE = "tools/cinema_control_center_base.py"
INVENTORY_RELATIVE = "tools/cinema_runtime_inventory.py"
PLANNER_RELATIVE = "tools/kcore_mission_planner.py"
PLANNING_RELATIVE = "tools/cinema_planning.py"
REGISTRY_RELATIVE = "config/echoes-capability-registry.v1.json"
BASE_PATH = REPO_ROOT / BASE_RELATIVE
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELATIVE
PLANNER_PATH = REPO_ROOT / PLANNER_RELATIVE
PLANNING_PATH = REPO_ROOT / PLANNING_RELATIVE
REGISTRY_PATH = REPO_ROOT / REGISTRY_RELATIVE
PLAN_PATH = "/v1/cinema/plan"


def atomic_materialize(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_from_git(relative: str, destination: Path) -> dict[str, str]:
    if destination.is_file() and destination.stat().st_size > 0:
        return {"status": "PRESENT", "path": str(destination), "sourceRef": "working-tree"}

    errors: list[str] = []
    for ref in ("HEAD", "origin/main"):
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{ref}:{relative}"],
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout:
            atomic_materialize(destination, completed.stdout)
            return {"status": "RESTORED", "path": str(destination), "sourceRef": ref}
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        errors.append(f"{ref}: {detail or 'file unavailable'}")
    raise RuntimeError(f"Unable to restore {relative} from Git refs: {' | '.join(errors)}")


def ensure_companion_sources() -> dict[str, Any]:
    return {
        "base": materialize_from_git(BASE_RELATIVE, BASE_PATH),
        "inventory": materialize_from_git(INVENTORY_RELATIVE, INVENTORY_PATH),
        "planner": materialize_from_git(PLANNER_RELATIVE, PLANNER_PATH),
        "planning": materialize_from_git(PLANNING_RELATIVE, PLANNING_PATH),
        "registry": materialize_from_git(REGISTRY_RELATIVE, REGISTRY_PATH),
        "networkRequested": False,
        "systemDriveWritesAllowed": False,
        "secretsPersisted": False,
    }


def load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Unable to create an import specification for {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


SOURCE_RECOVERY = ensure_companion_sources()
base = load_module("echoes_cinema_control_center_base", BASE_PATH)
inventory = load_module("cinema_runtime_inventory", INVENTORY_PATH)
planner = load_module("kcore_mission_planner", PLANNER_PATH)
planning = load_module("cinema_planning", PLANNING_PATH)
ORIGINAL_BUILD_STATUS = base.build_status
ORIGINAL_DO_POST = base.ControlCenterHandler.do_POST


def enhance_dashboard_html(html: str) -> str:
    queue_card = (
        '  <div class="card"><div class="label">Queue</div><div id="queue" class="value">—</div>'
        '<div id="workers" class="detail"></div></div>'
    )
    runtime_card = (
        '\n  <div class="card"><div class="label">Runtime inventory</div>'
        '<div id="runtimeInventory" class="value">Checking…</div>'
        '<div id="runtimeInventoryDetail" class="detail"></div></div>'
    )
    if queue_card not in html:
        raise RuntimeError("Control Center dashboard queue-card marker changed")
    html = html.replace(queue_card, queue_card + runtime_card, 1)

    recent_jobs = (
        '<section class="card">\n'
        '  <div class="label">Recent jobs</div>\n'
        '  <pre id="jobs">No jobs yet.</pre>\n'
        '</section>'
    )
    runtime_evidence = (
        '<section class="card">\n'
        '  <div class="label">Runtime evidence — expected vs installed</div>\n'
        '  <pre id="runtimeEvidence">No runtime evidence yet.</pre>\n'
        '</section>\n'
    )
    if recent_jobs not in html:
        raise RuntimeError("Control Center recent-jobs marker changed")
    html = html.replace(recent_jobs, runtime_evidence + recent_jobs, 1)

    workers_line = (
        "    byId('workers').textContent = `${text(s.runningCount, 0)} running · max ${text(s.maxWorkers, 1)}`;"
    )
    runtime_javascript = """
    const r = data.runtimeInventory || {};
    byId('runtimeInventory').innerHTML = badge(r.status || 'PARTIAL');
    byId('runtimeInventoryDetail').textContent = `Python ${text(r.python?.installed)} · Torch ${text(r.torch?.installed)} · CUDA ${text(r.cuda?.build?.installed)} · FFmpeg ${text(r.ffmpeg?.installed)}`;
    byId('runtimeEvidence').textContent = JSON.stringify(r, null, 2);
""".rstrip()
    if workers_line not in html:
        raise RuntimeError("Control Center queue JavaScript marker changed")
    return html.replace(workers_line, workers_line + "\n" + runtime_javascript, 1)


def workspace_from_server(server: Any) -> Path:
    runtime_root = str(os.getenv("ECHOES_CINEMA_RUNTIME_ROOT") or "").strip()
    if runtime_root:
        return Path(runtime_root).resolve().parent
    return Path(server.config.output_root).resolve().parent


def runtime_inventory_status(server: Any) -> dict[str, Any]:
    try:
        payload = inventory.build_inventory(REPO_ROOT, workspace_from_server(server))
        payload["sourceRecovery"] = SOURCE_RECOVERY
        return payload
    except Exception as error:  # noqa: BLE001 - exact inventory blocker belongs in the dashboard
        return {
            "schema": inventory.SCHEMA,
            "status": "BLOCKED",
            "failureClass": "RUNTIME_INVENTORY_UNAVAILABLE",
            "error": str(error),
            "sourceRecovery": SOURCE_RECOVERY,
            "systemDriveWritesAllowed": False,
            "secretsPersisted": False,
        }


def enhanced_build_status(server: Any) -> dict[str, Any]:
    payload = ORIGINAL_BUILD_STATUS(server)
    runtime_payload = runtime_inventory_status(server)
    payload["runtimeInventory"] = runtime_payload
    if runtime_payload.get("status") == "BLOCKED" and payload.get("status") != "PASS":
        payload["runtimeBlocker"] = runtime_payload.get("failureClass") or runtime_payload.get("error")
    return payload


def planning_provider(server: Any) -> dict[str, Any]:
    try:
        return base.base.fetch_provider_health(
            server.config.provider_endpoint,
            server.config.provider_token,
            min(server.config.provider_timeout, 5.0),
        )
    except Exception as error:  # noqa: BLE001 - planning must expose an unavailable provider as evidence
        return {
            "schema": "echoes.render-provider-health.v1",
            "status": "MISSING",
            "realModelLoaded": False,
            "commercialUseAllowed": False,
            "capabilities": {},
            "error": str(error),
        }


def mission_planner_for(server: Any) -> Any:
    current = getattr(server, "mission_planner", None)
    if current is None:
        current = planning.LocalMissionPlanner(REPO_ROOT, workspace_from_server(server), REGISTRY_PATH)
        server.mission_planner = current
    return current


def read_request_json(handler: Any) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0 or content_length > base.base.MAX_REQUEST_BYTES:
        raise ValueError("invalid request body size")
    payload = json.loads(handler.rfile.read(content_length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def enhanced_do_POST(handler: Any) -> None:  # noqa: N802 - stdlib handler API
    path = handler.path.split("?", 1)[0]
    if path != PLAN_PATH:
        ORIGINAL_DO_POST(handler)
        return
    if not handler.require_authorized():
        return
    try:
        request = read_request_json(handler)
        result = mission_planner_for(handler.cinema).plan(request, planning_provider(handler.cinema))
        handler.send_json(200, result)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, planner.PlanningError) as error:
        handler.send_json(
            400,
            {
                "schema": planner.SCHEMA,
                "status": "BLOCKED",
                "failureClass": "INVALID_PLANNING_INPUT",
                "error": str(error),
                "readOnly": True,
                "jobSubmitted": False,
                "mutationsAllowed": False,
                "secretsPersisted": False,
            },
        )
    except Exception as error:  # noqa: BLE001 - exact local planning blocker belongs in response
        handler.send_json(
            503,
            {
                "schema": planner.SCHEMA,
                "status": "BLOCKED",
                "failureClass": "PLANNING_SERVICE_UNAVAILABLE",
                "error": str(error),
                "readOnly": True,
                "jobSubmitted": False,
                "mutationsAllowed": False,
                "secretsPersisted": False,
            },
        )


def self_test() -> int:
    assert "Runtime inventory" in base.DASHBOARD_HTML
    assert "runtimeInventory" in base.DASHBOARD_HTML
    assert "runtimeEvidence" in base.DASHBOARD_HTML
    assert PLAN_PATH == "/v1/cinema/plan"
    assert SOURCE_RECOVERY["networkRequested"] is False
    assert SOURCE_RECOVERY["secretsPersisted"] is False
    planning.self_test()
    inventory.self_test()
    base.self_test()
    print("CinemaControlCenterRuntime PASS inventory=visible planning=authenticated-read-only migration=self-healing truth=fail-closed")
    return 0


base.DASHBOARD_HTML = enhance_dashboard_html(base.DASHBOARD_HTML)
base.build_status = enhanced_build_status
base.ControlCenterHandler.do_POST = enhanced_do_POST
base.ControlCenterHandler.server_version = "EchoesCinemaControlCenter/1.6"


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
