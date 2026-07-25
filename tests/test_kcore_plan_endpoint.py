#!/usr/bin/env python3
"""Cross-platform HTTP proof for authenticated read-only K-Core planning."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import cinema_control_center as control  # noqa: E402
import cinema_job_service as service  # noqa: E402


PROVIDER = {
    "schema": "echoes.render-provider-health.v1",
    "status": "PASS",
    "realModelLoaded": True,
    "commercialUseAllowed": True,
    "gpu": {"vramGiB": 6.0},
    "capabilities": {
        "textToVideo": True,
        "referenceImage": True,
        "subjectIdentity": True,
    },
}
INVENTORY = {
    "schema": "echoes.cinema-runtime-inventory.v1",
    "status": "PASS",
    "cuda": {"available": True},
}


class FixturePlanner:
    def plan(self, request: dict[str, object], provider: dict[str, object]) -> dict[str, object]:
        registry = control.planner.load_object(control.REGISTRY_PATH, "registry")
        return control.planning.plan_with_evidence(registry, request, INVENTORY, provider)


def post(url: str, payload: dict[str, object], token: str | None) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-kcore-plan-endpoint-") as temporary:
        root = Path(temporary)
        sections = root / "sections"
        audio = root / "audio"
        output = root / "jobs"
        sections.mkdir()
        audio.mkdir()
        output.mkdir()
        runner = root / "runner.py"
        runner.write_text("raise SystemExit('planning endpoint must never run this file')\n", encoding="utf-8")
        config = service.ServiceConfig(
            token="service-plan-token",
            host="127.0.0.1",
            port=0,
            manifest_cli=None,
            runner=runner,
            sections_root=sections,
            audio_root=audio,
            output_root=output,
            provider_endpoint="http://127.0.0.1:9/v1/render",
            provider_token="provider-plan-token",
            provider_timeout=1.0,
            max_workers=1,
        )
        server = service.CinemaServer(("127.0.0.1", 0), control.base.ControlCenterHandler)
        server.config = config
        server.registry = service.JobRegistry(config)
        server.mission_planner = FixturePlanner()
        control.planning_provider = lambda _server: PROVIDER
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}{control.PLAN_PATH}"
        request = {
            "schema": control.planning.REQUEST_SCHEMA,
            "jobId": "endpoint-plan-001",
            "missionType": "music_video",
            "requireIdentity": True,
            "commercialUse": True,
            "cloudAllowed": False,
            "minimumQuality": 85,
        }
        try:
            code, unauthorized = post(url, request, None)
            assert code == 401 and unauthorized["status"] == "FAILED"
            code, planned = post(url, request, "service-plan-token")
            assert code == 200
            assert planned["schema"] == "kcore.execution-plan.v1"
            assert planned["status"] == "PLANNED"
            assert planned["selectedPipeline"] == "cinema_identity_local_v1"
            assert planned["readOnly"] is True
            assert planned["jobSubmitted"] is False
            assert planned["mutationsAllowed"] is False
            assert server.registry.jobs == {}
            assert not any(output.iterdir())
            code, invalid = post(url, {**request, "shell": "whoami"}, "service-plan-token")
            assert code == 400
            assert invalid["failureClass"] == "INVALID_PLANNING_INPUT"
            assert invalid["jobSubmitted"] is False
        finally:
            server.shutdown()
            server.server_close()
            server.registry.executor.shutdown(wait=False, cancel_futures=True)
            thread.join(timeout=5)
    print("KCorePlanEndpoint PASS auth=required read-only=true job-submitted=false invalid-fields=blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
