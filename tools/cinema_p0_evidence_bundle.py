#!/usr/bin/env python3
"""Collect one truthful, portable evidence bundle after a Cinema P0 run.

The collector is standard-library only. It never installs or downloads anything.
It gathers the current proof, machine/preflight reports, provider logs, final media,
and a human-readable first-blocker summary into one ZIP on the non-system drive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "echoes.cinema-p0-evidence-bundle.v1"
TEXT_EXTENSIONS = {".json", ".log", ".txt", ".csv", ".md"}
REDACTIONS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+\-/=]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(ECHOES_RENDER_TOKEN\s*[:=]\s*)[^\s\"']{16,}"), r"\1[REDACTED]"),
    (re.compile(r'(?i)("token"\s*:\s*")[^"]+(\")'), r"\1[REDACTED]\2"),
)

PROOF_CANDIDATES = (
    "preflight-report.json",
    "gpu-report.json",
    "provider-health.json",
    "provider.log",
    "provider-error.log",
    "job-result.json",
    "video-qc.json",
    "render-manifest.json",
    "render-state.json",
    "resume-plan.json",
    "proof-audio.wav",
)
WORKSPACE_CANDIDATES = (
    "storage-report.json",
    "cinema-bootstrap-report.json",
    "cleanup-before-run.json",
    "cleanup-after-run.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def redact_text(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def copy_evidence(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in TEXT_EXTENSIONS:
        try:
            text = source.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            shutil.copy2(source, destination)
        else:
            destination.write_text(redact_text(text), encoding="utf-8", newline="\n")
    else:
        shutil.copy2(source, destination)


def tail_nonempty(path: Path, limit: int = 12) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    return [redact_text(line.strip()) for line in lines if line.strip()][-limit:]


def infer_truth_status(proof_dir: Path) -> tuple[str, str]:
    preflight = load_json(proof_dir / "preflight-report.json")
    health = load_json(proof_dir / "provider-health.json")
    result = load_json(proof_dir / "job-result.json")
    qc = load_json(proof_dir / "video-qc.json")
    top_level_mp4 = [path for path in proof_dir.glob("*.mp4") if path.is_file() and path.stat().st_size > 0]

    if preflight and preflight.get("status") != "PASS":
        blockers = preflight.get("blockers") or []
        return "FAILED", "P0 preflight failed: " + ", ".join(str(item) for item in blockers)
    if health and health.get("loadError"):
        return "FAILED", f"Model load failed: {health['loadError']}"
    if result and result.get("status") != "PASS":
        return "FAILED", f"Cinema job failed: {result.get('error') or 'job-result status is not PASS'}"
    if result and result.get("status") == "PASS" and result.get("backendStatus") == "REAL":
        if qc and qc.get("status") == "PASS" and top_level_mp4:
            return "REAL", "Model load, real backend, final MP4, and video QC all passed."
        return "PARTIAL", "The job reports REAL, but final MP4 or video-qc PASS evidence is missing."

    provider_error = tail_nonempty(proof_dir / "provider-error.log")
    if provider_error:
        return "MISSING", provider_error[-1]
    return "MISSING", "No complete REAL result exists yet; inspect the included missing-file and log evidence."


def collect_sources(workspace: Path, proof_dir: Path) -> list[tuple[Path, Path]]:
    sources: list[tuple[Path, Path]] = []
    seen: set[Path] = set()

    for relative in PROOF_CANDIDATES:
        source = proof_dir / relative
        if source.is_file():
            sources.append((source, Path("proof") / relative))
            seen.add(source.resolve())
    for source in sorted(proof_dir.glob("*.mp4")):
        if source.is_file() and source.resolve() not in seen:
            sources.append((source, Path("proof") / source.name))
            seen.add(source.resolve())
    for relative in WORKSPACE_CANDIDATES:
        source = workspace / relative
        if source.is_file():
            sources.append((source, Path("workspace") / relative))
    return sources


def build_bundle(workspace: Path, proof_dir: Path, output: Path, run_error: str | None = None) -> dict[str, Any]:
    workspace = workspace.resolve()
    proof_dir = proof_dir.resolve()
    output = output.resolve()
    if str(output).lower().startswith("c:\\"):
        raise RuntimeError(f"P0 evidence ZIP must not target drive C: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    truth_status, first_blocker = infer_truth_status(proof_dir)
    if run_error:
        first_blocker = redact_text(run_error.strip()) or first_blocker

    sources = collect_sources(workspace, proof_dir)
    expected = set(PROOF_CANDIDATES)
    present = {relative.name for _, relative in sources if relative.parts and relative.parts[0] == "proof"}
    missing = sorted(expected - present)

    with tempfile.TemporaryDirectory(prefix="echoes-p0-evidence-", dir=workspace / "temp") as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir(parents=True)
        entries: list[dict[str, Any]] = []
        for source, relative in sources:
            destination = staging / relative
            copy_evidence(source, destination)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "sizeBytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    "source": str(source),
                }
            )

        index = {
            "schema": SCHEMA,
            "timestampUtc": utc_now(),
            "truthStatus": truth_status,
            "firstBlocker": first_blocker,
            "workspace": str(workspace),
            "proofDirectory": str(proof_dir),
            "outputZip": str(output),
            "systemDriveWritesAllowed": False,
            "redactionApplied": True,
            "includedFileCount": len(entries),
            "missingExpectedProofFiles": missing,
            "files": entries,
        }
        (staging / "evidence-index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        summary = (
            "ECHOES CINEMA P0 EVIDENCE\n"
            "==========================\n"
            f"Truth status: {truth_status}\n"
            f"First blocker: {first_blocker}\n"
            f"Included files: {len(entries)}\n"
            f"Missing expected proof files: {', '.join(missing) if missing else 'none'}\n"
            "Drive C selected for evidence: no\n"
            "\nOpen evidence-index.json for hashes and exact paths.\n"
        )
        (staging / "READ_ME_FIRST.txt").write_text(summary, encoding="utf-8", newline="\n")

        fd, temporary_zip_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
        os.close(fd)
        temporary_zip = Path(temporary_zip_name)
        try:
            with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(staging).as_posix())
            os.replace(temporary_zip, output)
        finally:
            temporary_zip.unlink(missing_ok=True)

    return index


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-p0-bundle-test-") as temporary:
        workspace = Path(temporary) / "D-drive-simulation" / "EchoesCinema"
        proof = workspace / "proofs" / "first-real-ai-clip"
        (workspace / "temp").mkdir(parents=True)
        proof.mkdir(parents=True)
        (proof / "preflight-report.json").write_text('{"status":"PASS"}\n', encoding="utf-8")
        (proof / "provider-error.log").write_text("Authorization: Bearer super-secret-token-value\nOOM test blocker\n", encoding="utf-8")
        output = workspace / "proofs" / "evidence" / "latest.zip"
        report = build_bundle(workspace, proof, output)
        assert report["truthStatus"] == "MISSING"
        assert report["firstBlocker"] == "OOM test blocker"
        assert output.is_file() and output.stat().st_size > 0
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            assert "READ_ME_FIRST.txt" in names
            assert "evidence-index.json" in names
            log = archive.read("proof/provider-error.log").decode("utf-8")
            assert "super-secret-token-value" not in log
            assert "[REDACTED]" in log
    print("CinemaP0EvidenceBundle PASS zip=validated redaction=validated blocker=validated")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(r"D:\A.I\EchoesCinema"))
    parser.add_argument("--proof-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--run-error", default="")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    workspace = args.workspace
    proof_dir = args.proof_dir or workspace / "proofs" / "first-real-ai-clip"
    output = args.output or workspace / "proofs" / "evidence" / "latest-p0-evidence.zip"
    report = build_bundle(workspace, proof_dir, output, args.run_error or None)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Cinema P0 evidence bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
