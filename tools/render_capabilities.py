#!/usr/bin/env python3
"""Validate Echoes render-provider capabilities against job requirements.

The module is shared by the HTTP worker and the Cinema job service. A provider
cannot accept a job merely because it reports ``realModelLoaded=true``; it must
also explicitly advertise every capability required by the manifest or service
request. Commercial jobs additionally require an explicit
``commercialUseAllowed=true`` declaration.
"""

from __future__ import annotations

from typing import Any, Iterable


SUPPORTED_CAPABILITIES = frozenset({"textToVideo", "referenceImage", "subjectIdentity"})


def task_requirements(tasks: Iterable[dict[str, Any]]) -> set[str]:
    required = {"textToVideo"}
    for task in tasks:
        continuity = task.get("continuity") if isinstance(task.get("continuity"), dict) else {}
        reference_asset = str(continuity.get("referenceAsset") or "").strip()
        subject_id = str(continuity.get("subjectId") or "").strip()
        try:
            identity_strength = float(continuity.get("strength", 0.0) or 0.0)
        except (TypeError, ValueError):
            identity_strength = 0.0
        if reference_asset:
            required.add("referenceImage")
        if reference_asset and subject_id and identity_strength > 0.0:
            required.add("subjectIdentity")
    return required


def normalize_explicit_requirements(requirements: Iterable[str] | None) -> set[str]:
    normalized = {str(item).strip() for item in requirements or () if str(item).strip()}
    unsupported = normalized - SUPPORTED_CAPABILITIES
    if unsupported:
        raise ValueError(f"unsupported render capability requirements: {sorted(unsupported)}")
    return normalized


def validate_provider_health(
    health: dict[str, Any],
    *,
    tasks: Iterable[dict[str, Any]] | None = None,
    explicit_requirements: Iterable[str] | None = None,
    require_real_model: bool,
    require_commercial_use: bool = False,
) -> set[str]:
    if health.get("schema") != "echoes.render-provider-health.v1":
        raise RuntimeError("provider health schema is unsupported")
    if health.get("status") != "PASS":
        raise RuntimeError("provider health is not PASS")
    if require_real_model and health.get("realModelLoaded") is not True:
        raise RuntimeError("provider has no verified real model loaded")
    if require_commercial_use and health.get("commercialUseAllowed") is not True:
        raise RuntimeError("provider is not approved for commercial renders")

    required = normalize_explicit_requirements(explicit_requirements)
    if tasks is not None:
        required.update(task_requirements(tasks))

    capabilities = health.get("capabilities")
    if not isinstance(capabilities, dict):
        if required:
            raise RuntimeError("provider health has no capability contract")
        return required

    missing = sorted(name for name in required if capabilities.get(name) is not True)
    if missing:
        raise RuntimeError(f"provider is missing required capabilities: {', '.join(missing)}")
    return required


def accepting_real_jobs(health: dict[str, Any]) -> bool:
    try:
        validate_provider_health(
            health,
            explicit_requirements={"textToVideo"},
            require_real_model=True,
        )
        return True
    except Exception:
        return False


def accepting_commercial_jobs(health: dict[str, Any]) -> bool:
    try:
        validate_provider_health(
            health,
            explicit_requirements={"textToVideo"},
            require_real_model=True,
            require_commercial_use=True,
        )
        return True
    except Exception:
        return False


def self_test() -> int:
    health = {
        "schema": "echoes.render-provider-health.v1",
        "status": "PASS",
        "realModelLoaded": True,
        "commercialUseAllowed": False,
        "capabilities": {
            "textToVideo": True,
            "referenceImage": False,
            "subjectIdentity": False,
        },
    }
    text_task = {"continuity": {"referenceAsset": "", "subjectId": "proof", "strength": 0.75}}
    assert validate_provider_health(health, tasks=[text_task], require_real_model=True) == {"textToVideo"}

    reference_task = {
        "continuity": {
            "referenceAsset": "references/artist.png",
            "subjectId": "artist",
            "strength": 0.9,
        }
    }
    try:
        validate_provider_health(health, tasks=[reference_task], require_real_model=True)
    except RuntimeError as error:
        assert "referenceImage" in str(error)
        assert "subjectIdentity" in str(error)
    else:
        raise AssertionError("reference/identity capability failure was not enforced")

    try:
        validate_provider_health(
            health,
            tasks=[text_task],
            require_real_model=True,
            require_commercial_use=True,
        )
    except RuntimeError as error:
        assert "commercial" in str(error)
    else:
        raise AssertionError("commercial-use failure was not enforced")

    assert accepting_real_jobs(health) is True
    assert accepting_commercial_jobs(health) is False
    print("RenderCapabilities self-test PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
