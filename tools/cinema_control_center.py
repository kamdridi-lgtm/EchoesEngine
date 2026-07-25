#!/usr/bin/env python3
"""Runtime-inventory enhanced entrypoint for the Echoes Cinema Control Center.

The canonical Control Center implementation is kept in cinema_control_center_base.py.
This small entrypoint can restore its companion modules from the current Git commit
or the already-fetched origin/main ref, then adds truthful runtime inventory data to
the localhost dashboard without weakening any existing authentication boundary.
"""

from __future__ import annotations

import importlib.util
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
BASE_PATH = REPO_ROOT / BASE_RELATIVE
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELATIVE


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
inventory = load_module("echoes_cinema_runtime_inventory", INVENTORY_PATH)
ORIGINAL_BUILD_STATUS = base.build_status


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


def self_test() -> int:
    assert "Runtime inventory" in base.DASHBOARD_HTML
    assert "runtimeInventory" in base.DASHBOARD_HTML
    assert "runtimeEvidence" in base.DASHBOARD_HTML
    assert SOURCE_RECOVERY["networkRequested"] is False
    assert SOURCE_RECOVERY["secretsPersisted"] is False
    inventory.self_test()
    base.self_test()
    print("CinemaControlCenterRuntime PASS inventory=visible migration=self-healing truth=fail-closed")
    return 0


base.DASHBOARD_HTML = enhance_dashboard_html(base.DASHBOARD_HTML)
base.build_status = enhanced_build_status
base.ControlCenterHandler.server_version = "EchoesCinemaControlCenter/1.5"


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
