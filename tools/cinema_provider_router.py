#!/usr/bin/env python3
"""Fail-closed Echoes Cinema provider routing and budget policy.

The router chooses only a provider candidate. It never claims that a render
completed or that a provider is REAL. Local proof jobs and commercial jobs are
kept in separate trust domains, remote commercial pricing must be explicit, and
the caller's budget is enforced before any render request is sent.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from render_capabilities import normalize_explicit_requirements, validate_provider_health

SCHEMA = "echoes.cinema-provider-route.v1"
CONFIG_SCHEMA = "echoes.cinema-provider-catalog.v1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_HEALTH_BYTES = 1024 * 1024
MAX_DURATION_SECONDS = 6 * 60 * 60
MAX_BUDGET_USD = 10000.0


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_number(value: Any, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def validate_endpoint(endpoint: str, *, remote: bool) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("provider endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("provider endpoint must not contain credentials")
    if parsed.fragment:
        raise ValueError("provider endpoint must not contain a URL fragment")
    if remote:
        if parsed.scheme != "https":
            raise ValueError("remote providers require HTTPS")
        if host in LOOPBACK_HOSTS:
            raise ValueError("remote provider cannot target loopback")
    elif host not in LOOPBACK_HOSTS:
        raise ValueError("local provider must target loopback")
    return urllib.parse.urlunparse(parsed)


def derive_health_url(endpoint: str, explicit: str | None) -> str:
    if explicit:
        parsed = urllib.parse.urlparse(explicit)
        endpoint_parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != endpoint_parsed.scheme or parsed.netloc != endpoint_parsed.netloc:
            raise ValueError("health URL must use the same scheme and authority as the render endpoint")
        return urllib.parse.urlunparse(parsed)
    parsed = urllib.parse.urlparse(endpoint)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


@dataclass(frozen=True)
class ProviderCandidate:
    name: str
    scope: str
    location: str
    endpoint: str
    health_url: str
    token_env: str
    priority: int
    expected_model_id: str
    expected_model_revision: str
    billing_mode: str
    fixed_usd: float
    usd_per_second: float
    pricing_verified: bool

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ProviderCandidate":
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 128:
            raise ValueError("provider name is required and must be at most 128 characters")
        scope = str(payload.get("scope") or "").strip()
        if scope not in {"proof", "commercial"}:
            raise ValueError(f"{name}: scope must be proof or commercial")
        location = str(payload.get("location") or "").strip()
        if location not in {"local", "remote"}:
            raise ValueError(f"{name}: location must be local or remote")

        endpoint = validate_endpoint(str(payload.get("endpoint") or ""), remote=location == "remote")
        health_url = derive_health_url(endpoint, str(payload.get("healthUrl") or "").strip() or None)
        token_env = str(payload.get("tokenEnv") or "").strip()
        if not token_env or not token_env.replace("_", "").isalnum() or token_env[0].isdigit():
            raise ValueError(f"{name}: tokenEnv must be an environment variable name")

        priority_raw = payload.get("priority", 50)
        if isinstance(priority_raw, bool):
            raise ValueError(f"{name}: priority must be an integer")
        priority = int(priority_raw)
        if priority < 0 or priority > 100:
            raise ValueError(f"{name}: priority must be between 0 and 100")

        expected_model_id = str(payload.get("expectedModelId") or "").strip()
        expected_model_revision = str(payload.get("expectedModelRevision") or "").strip()
        if scope == "commercial" and (not expected_model_id or not expected_model_revision):
            raise ValueError(f"{name}: commercial providers require exact model id and revision")

        pricing = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else {}
        billing_mode = str(pricing.get("billingMode") or "").strip()
        if location == "local":
            if billing_mode != "local":
                raise ValueError(f"{name}: local providers require pricing.billingMode=local")
            fixed_usd = 0.0
            usd_per_second = 0.0
            pricing_verified = True
        else:
            if billing_mode != "metered":
                raise ValueError(f"{name}: remote providers require pricing.billingMode=metered")
            fixed_usd = require_number(
                pricing.get("fixedUsd", 0.0),
                f"{name}.pricing.fixedUsd",
                minimum=0.0,
                maximum=MAX_BUDGET_USD,
            )
            usd_per_second = require_number(
                pricing.get("usdPerSecond"),
                f"{name}.pricing.usdPerSecond",
                minimum=0.0,
                maximum=MAX_BUDGET_USD,
            )
            pricing_verified = pricing.get("verified") is True
            if not pricing_verified:
                raise ValueError(f"{name}: remote pricing must be explicitly verified")

        return cls(
            name=name,
            scope=scope,
            location=location,
            endpoint=endpoint,
            health_url=health_url,
            token_env=token_env,
            priority=priority,
            expected_model_id=expected_model_id,
            expected_model_revision=expected_model_revision,
            billing_mode=billing_mode,
            fixed_usd=fixed_usd,
            usd_per_second=usd_per_second,
            pricing_verified=pricing_verified,
        )

    def estimate_cost(self, duration_seconds: float) -> float:
        return round(self.fixed_usd + self.usd_per_second * duration_seconds, 6)


def load_catalog(path: Path) -> list[ProviderCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"provider catalog schema must be {CONFIG_SCHEMA}")
    providers = payload.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("provider catalog must contain a non-empty providers array")
    candidates = [ProviderCandidate.from_payload(item) for item in providers if isinstance(item, dict)]
    if len(candidates) != len(providers):
        raise ValueError("every provider catalog entry must be an object")
    names = [candidate.name for candidate in candidates]
    if len(set(names)) != len(names):
        raise ValueError("provider names must be unique")
    return candidates


def fetch_health(candidate: ProviderCandidate, timeout: float) -> dict[str, Any]:
    token = os.getenv(candidate.token_env, "")
    if not token:
        raise RuntimeError(f"required provider token environment variable is missing: {candidate.token_env}")
    request = urllib.request.Request(
        candidate.health_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "EchoesCinemaProviderRouter/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_HEALTH_BYTES + 1)
            status_code = response.status
            content_type = response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"health HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"health connection failed: {error.reason}") from error
    if len(body) > MAX_HEALTH_BYTES:
        raise RuntimeError("health response exceeded size limit")
    if status_code != 200 or content_type != "application/json":
        raise RuntimeError(f"health returned HTTP {status_code} content-type {content_type}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("health returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("health must be a JSON object")
    return payload


def validate_identity(candidate: ProviderCandidate, health: dict[str, Any], usage: str) -> None:
    model_id = str(health.get("modelId") or "")
    model_revision = str(health.get("modelRevision") or "")
    if candidate.expected_model_id and model_id != candidate.expected_model_id:
        raise RuntimeError(
            f"model id mismatch: expected {candidate.expected_model_id}, received {model_id or 'missing'}"
        )
    if candidate.expected_model_revision and model_revision != candidate.expected_model_revision:
        raise RuntimeError(
            "model revision mismatch: expected "
            f"{candidate.expected_model_revision}, received {model_revision or 'missing'}"
        )
    if usage == "commercial":
        license_name = str(health.get("license") or "").strip()
        if not license_name:
            raise RuntimeError("commercial provider health has no license declaration")


def eligible_scope(candidate: ProviderCandidate, usage: str, allow_commercial_fallback: bool) -> bool:
    if usage == "commercial":
        return candidate.scope == "commercial"
    return candidate.scope == "proof" or (allow_commercial_fallback and candidate.scope == "commercial")


def route_provider(
    candidates: Iterable[ProviderCandidate],
    *,
    usage: str,
    duration_seconds: float,
    max_cost_usd: float,
    required_capabilities: Iterable[str],
    allow_commercial_fallback: bool = False,
    health_evidence: dict[str, dict[str, Any]] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if usage not in {"proof", "commercial"}:
        raise ValueError("usage must be proof or commercial")
    duration = require_number(
        duration_seconds,
        "durationSeconds",
        minimum=0.1,
        maximum=MAX_DURATION_SECONDS,
    )
    budget = require_number(max_cost_usd, "maxCostUsd", minimum=0.0, maximum=MAX_BUDGET_USD)
    requirements = normalize_explicit_requirements(required_capabilities)
    requirements.add("textToVideo")

    evaluations: list[dict[str, Any]] = []
    acceptable: list[tuple[int, float, str, ProviderCandidate, dict[str, Any], set[str]]] = []

    for candidate in candidates:
        evaluation: dict[str, Any] = {
            "name": candidate.name,
            "scope": candidate.scope,
            "location": candidate.location,
            "priority": candidate.priority,
            "eligible": False,
            "estimatedCostUsd": candidate.estimate_cost(duration),
            "decision": "REJECTED",
        }
        if not eligible_scope(candidate, usage, allow_commercial_fallback):
            evaluation["blocker"] = "provider scope is not eligible for this usage"
            evaluations.append(evaluation)
            continue
        evaluation["eligible"] = True
        estimated = evaluation["estimatedCostUsd"]
        if estimated > budget:
            evaluation["blocker"] = f"estimated cost {estimated:.6f} USD exceeds budget {budget:.6f} USD"
            evaluations.append(evaluation)
            continue
        try:
            if health_evidence is not None:
                health = health_evidence.get(candidate.name)
                if not isinstance(health, dict):
                    raise RuntimeError("no health evidence supplied for provider")
            else:
                health = fetch_health(candidate, timeout)
            validated = validate_provider_health(
                health,
                explicit_requirements=requirements,
                require_real_model=True,
                require_commercial_use=usage == "commercial",
            )
            validate_identity(candidate, health, usage)
        except Exception as error:  # noqa: BLE001 - routing report must retain exact blocker
            evaluation["blocker"] = str(error)
            evaluations.append(evaluation)
            continue

        evaluation.update(
            {
                "decision": "ACCEPTABLE",
                "modelId": health.get("modelId"),
                "modelRevision": health.get("modelRevision"),
                "commercialUseAllowed": health.get("commercialUseAllowed") is True,
                "requiredCapabilities": sorted(validated),
            }
        )
        evaluations.append(evaluation)
        acceptable.append(
            (
                -candidate.priority,
                float(estimated),
                candidate.name,
                candidate,
                health,
                validated,
            )
        )

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "PARTIAL",
        "classification": "CANDIDATE_ONLY",
        "usage": usage,
        "durationSeconds": duration,
        "maxCostUsd": budget,
        "requiredCapabilities": sorted(requirements),
        "allowCommercialFallbackForProof": allow_commercial_fallback,
        "selectionOnly": True,
        "renderCompleted": False,
        "realModelLoadedByRouter": False,
        "evaluations": evaluations,
    }
    if not acceptable:
        report.update(
            {
                "decision": "BLOCKED",
                "blocker": "no provider passed scope, budget, health, capability, identity, and license gates",
            }
        )
        return report

    _, estimated, _, selected, health, validated = sorted(acceptable)[0]
    report.update(
        {
            "decision": "ROUTE_SELECTED",
            "selectedProvider": {
                "name": selected.name,
                "scope": selected.scope,
                "location": selected.location,
                "endpoint": selected.endpoint,
                "healthUrl": selected.health_url,
                "tokenEnv": selected.token_env,
                "priority": selected.priority,
                "modelId": health.get("modelId"),
                "modelRevision": health.get("modelRevision"),
                "license": health.get("license"),
                "commercialUseAllowed": health.get("commercialUseAllowed") is True,
                "requiredCapabilities": sorted(validated),
                "estimatedCostUsd": estimated,
                "budgetRemainingUsd": round(budget - estimated, 6),
                "billingMode": selected.billing_mode,
                "pricingVerified": selected.pricing_verified,
            },
            "nextProofRequired": "provider-request-render-qc-sha256",
        }
    )
    return report


def self_test() -> int:
    proof_candidate = ProviderCandidate.from_payload(
        {
            "name": "local-proof",
            "scope": "proof",
            "location": "local",
            "endpoint": "http://127.0.0.1:8081/v1/render",
            "tokenEnv": "ECHOES_LOCAL_PROOF_TOKEN",
            "priority": 100,
            "expectedModelId": "ali-vilab/text-to-video-ms-1.7b",
            "expectedModelRevision": "proof-revision",
            "pricing": {"billingMode": "local"},
        }
    )
    remote_candidate = ProviderCandidate.from_payload(
        {
            "name": "remote-commercial",
            "scope": "commercial",
            "location": "remote",
            "endpoint": "https://cinema.example.test/v1/render",
            "tokenEnv": "ECHOES_REMOTE_COMMERCIAL_TOKEN",
            "priority": 80,
            "expectedModelId": "zai-org/CogVideoX-2b",
            "expectedModelRevision": "102080da924c0ab684abeeca4b061ec7dfb7d40c",
            "pricing": {
                "billingMode": "metered",
                "fixedUsd": 0.25,
                "usdPerSecond": 0.05,
                "verified": True,
            },
        }
    )
    proof_health = {
        "schema": "echoes.render-provider-health.v1",
        "status": "PASS",
        "realModelLoaded": True,
        "modelId": "ali-vilab/text-to-video-ms-1.7b",
        "modelRevision": "proof-revision",
        "commercialUseAllowed": False,
        "license": "CC-BY-NC-4.0",
        "capabilities": {
            "textToVideo": True,
            "referenceImage": False,
            "subjectIdentity": False,
        },
    }
    commercial_health = {
        "schema": "echoes.render-provider-health.v1",
        "status": "PASS",
        "realModelLoaded": True,
        "modelId": "zai-org/CogVideoX-2b",
        "modelRevision": "102080da924c0ab684abeeca4b061ec7dfb7d40c",
        "commercialUseAllowed": True,
        "license": "Apache-2.0",
        "capabilities": {
            "textToVideo": True,
            "referenceImage": True,
            "subjectIdentity": True,
        },
    }
    evidence = {
        "local-proof": proof_health,
        "remote-commercial": commercial_health,
    }

    proof = route_provider(
        [proof_candidate, remote_candidate],
        usage="proof",
        duration_seconds=4,
        max_cost_usd=0,
        required_capabilities={"textToVideo"},
        health_evidence=evidence,
    )
    assert proof["decision"] == "ROUTE_SELECTED"
    assert proof["selectedProvider"]["name"] == "local-proof"
    assert proof["selectedProvider"]["estimatedCostUsd"] == 0.0

    commercial = route_provider(
        [proof_candidate, remote_candidate],
        usage="commercial",
        duration_seconds=10,
        max_cost_usd=1.0,
        required_capabilities={"textToVideo", "subjectIdentity"},
        health_evidence=evidence,
    )
    assert commercial["decision"] == "ROUTE_SELECTED"
    assert commercial["selectedProvider"]["name"] == "remote-commercial"
    assert commercial["selectedProvider"]["estimatedCostUsd"] == 0.75
    assert commercial["selectedProvider"]["commercialUseAllowed"] is True

    over_budget = route_provider(
        [remote_candidate],
        usage="commercial",
        duration_seconds=10,
        max_cost_usd=0.5,
        required_capabilities={"textToVideo"},
        health_evidence=evidence,
    )
    assert over_budget["decision"] == "BLOCKED"
    assert "exceeds budget" in over_budget["evaluations"][0]["blocker"]

    wrong_revision = dict(commercial_health)
    wrong_revision["modelRevision"] = "wrong"
    revision_blocked = route_provider(
        [remote_candidate],
        usage="commercial",
        duration_seconds=4,
        max_cost_usd=2.0,
        required_capabilities={"textToVideo"},
        health_evidence={"remote-commercial": wrong_revision},
    )
    assert revision_blocked["decision"] == "BLOCKED"
    assert "revision mismatch" in revision_blocked["evaluations"][0]["blocker"]

    no_identity = dict(commercial_health)
    no_identity["capabilities"] = dict(commercial_health["capabilities"])
    no_identity["capabilities"]["subjectIdentity"] = False
    capability_blocked = route_provider(
        [remote_candidate],
        usage="commercial",
        duration_seconds=4,
        max_cost_usd=2.0,
        required_capabilities={"subjectIdentity"},
        health_evidence={"remote-commercial": no_identity},
    )
    assert capability_blocked["decision"] == "BLOCKED"
    assert "subjectIdentity" in capability_blocked["evaluations"][0]["blocker"]

    try:
        ProviderCandidate.from_payload(
            {
                "name": "unsafe-remote",
                "scope": "commercial",
                "location": "remote",
                "endpoint": "http://example.test/v1/render",
                "tokenEnv": "TOKEN",
                "expectedModelId": "model",
                "expectedModelRevision": "revision",
                "pricing": {
                    "billingMode": "metered",
                    "usdPerSecond": 0.1,
                    "verified": True,
                },
            }
        )
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("remote HTTP endpoint was not rejected")

    print("CinemaProviderRouter PASS proof=local commercial=remote budget=validated revision=validated")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--usage", choices=("proof", "commercial"), default="proof")
    parser.add_argument("--duration-seconds", type=float, default=4.0)
    parser.add_argument("--max-cost-usd", type=float, default=0.0)
    parser.add_argument("--require-capability", action="append", default=[])
    parser.add_argument("--allow-commercial-fallback", action="store_true")
    parser.add_argument("--health-evidence", type=Path)
    parser.add_argument("--health-timeout", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    if args.catalog is None or not args.catalog.is_file():
        raise SystemExit("--catalog must point to a provider catalog JSON file")
    if args.health_timeout <= 0 or args.health_timeout > 120:
        raise SystemExit("--health-timeout must be between 0 and 120 seconds")

    candidates = load_catalog(args.catalog)
    health_evidence: dict[str, dict[str, Any]] | None = None
    if args.health_evidence is not None:
        payload = json.loads(args.health_evidence.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise SystemExit("--health-evidence must contain a JSON object keyed by provider name")
        health_evidence = {
            str(name): health
            for name, health in payload.items()
            if isinstance(health, dict)
        }

    report = route_provider(
        candidates,
        usage=args.usage,
        duration_seconds=args.duration_seconds,
        max_cost_usd=args.max_cost_usd,
        required_capabilities=args.require_capability,
        allow_commercial_fallback=args.allow_commercial_fallback,
        health_evidence=health_evidence,
        timeout=args.health_timeout,
    )
    if args.output is not None:
        atomic_write_json(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["decision"] == "ROUTE_SELECTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
