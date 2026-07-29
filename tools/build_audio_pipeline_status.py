#!/usr/bin/env python3
"""Build a truthful cross-stage status board for Echoes audio jobs.

The tool discovers existing JSON evidence and links records by source/vocal hashes.
It never reads audio, reruns inference, changes evidence, or authorizes execution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

OUTPUT_SCHEMA = "echoes.audio-pipeline-status-board.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STAGE_ORDER = (
    "analysis",
    "stem_separation",
    "technical_qc",
    "human_stem_review",
    "rvc_input_ready",
    "rvc_comparison_planned",
    "rvc_comparison_run",
    "human_model_selection",
)
SCHEMA_STAGE = {
    "echoes.stem-separation-run.v1": "stem_separation",
    "echoes.stem-technical-quality.v1": "technical_qc",
    "echoes.stem-listening-review.v1": "human_stem_review",
    "echoes.rvc-input-manifest.v1": "rvc_input_ready",
    "echoes.rvc-model-comparison-plan.v1": "rvc_comparison_planned",
    "echoes.recovered-rvc-comparison-run.v1": "rvc_comparison_run",
    "echoes.rvc-comparison-listening-review.v1": "human_model_selection",
}
SUCCESS_STATUS = {
    "analysis": {"PASS"},
    "stem_separation": {"PASS"},
    "technical_qc": {"PASS"},
    "human_stem_review": {"APPROVED"},
    "rvc_input_ready": {"READY"},
    "rvc_comparison_planned": {"READY"},
    "rvc_comparison_run": {"PASS"},
    "human_model_selection": {"APPROVED"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_sha(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SHA256_RE.fullmatch(candidate) else ""


def dig(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def iter_json_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved.is_file() and resolved.suffix.lower() == ".json":
            if resolved not in seen:
                seen.add(resolved)
                yield resolved
            continue
        if not resolved.is_dir():
            continue
        for path in resolved.rglob("*.json"):
            if any(part in {".git", ".venv", "venv", "node_modules"} for part in path.parts):
                continue
            if path not in seen:
                seen.add(path)
                yield path


def extract_hashes(schema: str, doc: dict[str, Any]) -> tuple[str, str]:
    source_sha = ""
    vocal_sha = ""
    if schema in {"echoes.stem-separation-run.v1", "echoes.stem-technical-quality.v1"}:
        source_sha = normalize_sha(dig(doc, "source", "sha256"))
    elif schema == "echoes.stem-listening-review.v1":
        source_sha = normalize_sha(dig(doc, "inputs", "sourceSha256"))
        stem_hashes = dig(doc, "inputs", "verifiedStemSha256")
        if isinstance(stem_hashes, dict):
            vocal_sha = normalize_sha(stem_hashes.get("vocals"))
    elif schema == "echoes.rvc-input-manifest.v1":
        source_sha = normalize_sha(dig(doc, "source", "sha256"))
        vocal_sha = normalize_sha(dig(doc, "vocalInput", "sha256"))
    elif schema == "echoes.rvc-model-comparison-plan.v1":
        vocal_sha = normalize_sha(dig(doc, "input", "vocalSha256"))
    elif schema == "echoes.recovered-rvc-comparison-run.v1":
        runs = doc.get("runs")
        if isinstance(runs, list) and runs and isinstance(runs[0], dict):
            vocal_sha = normalize_sha(runs[0].get("inputSha256"))
    elif schema == "echoes.rvc-comparison-listening-review.v1":
        runs = dig(doc, "comparison", "runs")
        if isinstance(runs, list) and runs and isinstance(runs[0], dict):
            vocal_sha = normalize_sha(runs[0].get("inputSha256"))
    return source_sha, vocal_sha


def display_name(schema: str, doc: dict[str, Any], path: Path) -> str:
    candidates = [
        dig(doc, "source", "name"),
        dig(doc, "vocalInput", "name"),
        dig(doc, "source", "path"),
        dig(doc, "vocalInput", "path"),
    ]
    if schema == "echoes.recovered-rvc-comparison-run.v1":
        runs = doc.get("runs")
        if isinstance(runs, list) and runs and isinstance(runs[0], dict):
            candidates.append(runs[0].get("inputPath"))
    if schema == "echoes.rvc-comparison-listening-review.v1":
        runs = dig(doc, "comparison", "runs")
        if isinstance(runs, list) and runs and isinstance(runs[0], dict):
            candidates.append(runs[0].get("inputPath"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return Path(text).name or text
    return path.stem


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, value: str) -> None:
        if value and value not in self.parent:
            self.parent[value] = value

    def find(self, value: str) -> str:
        self.add(value)
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        if not left or not right:
            return
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def evidence_record(stage: str, schema: str, status: str, path: Path, doc: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stage": stage,
        "schema": schema,
        "status": status,
        "successful": status in SUCCESS_STATUS.get(stage, set()),
        "path": str(path.resolve()),
        "modifiedAtUtc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if stage == "human_model_selection":
        record["selectedLabel"] = dig(doc, "decision", "selectedLabel")
        record["reviewer"] = doc.get("reviewer")
    elif stage == "human_stem_review":
        record["reviewer"] = doc.get("reviewer")
        record["decision"] = doc.get("decision")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text-output", type=Path)
    args = parser.parse_args()

    union = UnionFind()
    raw_records: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    scanned = 0

    for path in iter_json_files(args.root):
        scanned += 1
        try:
            doc = load_json(path)
        except Exception as exc:
            unreadable.append({"path": str(path.resolve()), "error": str(exc)})
            continue

        schema = str(doc.get("schema") or "")
        if schema == "echoes.autopilot-report.v1":
            items = doc.get("items")
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                source_sha = normalize_sha(item.get("sourceSha256") or item.get("sha256"))
                if not source_sha:
                    continue
                union.add(source_sha)
                status = str(item.get("status") or "UNKNOWN")
                raw_records.append({
                    "sourceSha": source_sha,
                    "vocalSha": "",
                    "name": Path(str(item.get("sourcePath") or item.get("path") or f"analysis-{index}")).name,
                    "evidence": {
                        "stage": "analysis",
                        "schema": schema,
                        "status": status,
                        "successful": status in SUCCESS_STATUS["analysis"],
                        "path": str(path.resolve()),
                        "itemIndex": index,
                        "jobId": item.get("jobId"),
                        "modifiedAtUtc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                    },
                })
            continue

        stage = SCHEMA_STAGE.get(schema)
        if not stage:
            continue
        source_sha, vocal_sha = extract_hashes(schema, doc)
        if not source_sha and not vocal_sha:
            continue
        union.add(source_sha or vocal_sha)
        union.add(vocal_sha or source_sha)
        if source_sha and vocal_sha:
            union.union(source_sha, vocal_sha)
        raw_records.append({
            "sourceSha": source_sha,
            "vocalSha": vocal_sha,
            "name": display_name(schema, doc, path),
            "evidence": evidence_record(stage, schema, str(doc.get("status") or "UNKNOWN"), path, doc),
        })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, list[str]] = defaultdict(list)
    source_hashes: dict[str, set[str]] = defaultdict(set)
    vocal_hashes: dict[str, set[str]] = defaultdict(set)

    for record in raw_records:
        identity = record["sourceSha"] or record["vocalSha"]
        root = union.find(identity)
        grouped[root].append(record["evidence"])
        if record["name"]:
            names[root].append(record["name"])
        if record["sourceSha"]:
            source_hashes[root].add(record["sourceSha"])
        if record["vocalSha"]:
            vocal_hashes[root].add(record["vocalSha"])

    songs: list[dict[str, Any]] = []
    for identity, records in grouped.items():
        by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_stage[record["stage"]].append(record)

        stage_summary: dict[str, Any] = {}
        highest_index = -1
        blockers: list[str] = []
        for index, stage in enumerate(STAGE_ORDER):
            entries = sorted(by_stage.get(stage, []), key=lambda item: item["modifiedAtUtc"])
            successful = [item for item in entries if item["successful"]]
            latest = entries[-1] if entries else None
            stage_summary[stage] = {
                "state": "COMPLETE" if successful else ("BLOCKED" if entries else "MISSING"),
                "latestStatus": latest["status"] if latest else None,
                "evidenceCount": len(entries),
                "latestEvidencePath": latest["path"] if latest else None,
            }
            if successful:
                highest_index = index
            elif entries:
                blockers.append(f"{stage}:{latest['status']}")

        current_stage = STAGE_ORDER[highest_index] if highest_index >= 0 else "none"
        next_stage = STAGE_ORDER[highest_index + 1] if highest_index + 1 < len(STAGE_ORDER) else None
        final_entries = by_stage.get("human_model_selection", [])
        selected = next((entry for entry in reversed(final_entries) if entry.get("successful")), None)
        chosen_name = names[identity][0] if names[identity] else identity

        songs.append({
            "identity": identity,
            "displayName": chosen_name,
            "sourceSha256": sorted(source_hashes[identity]) or None,
            "vocalSha256": sorted(vocal_hashes[identity]) or None,
            "currentStage": current_stage,
            "nextRequiredStage": next_stage,
            "pipelineComplete": selected is not None,
            "selectedRvcLabel": selected.get("selectedLabel") if selected else None,
            "blockers": blockers,
            "stages": stage_summary,
            "evidence": sorted(records, key=lambda item: (STAGE_ORDER.index(item["stage"]), item["modifiedAtUtc"])),
        })

    songs.sort(key=lambda item: (not item["pipelineComplete"], item["displayName"].lower()))
    report = {
        "schema": OUTPUT_SCHEMA,
        "version": "1.0.0",
        "status": "PASS",
        "generatedAtUtc": utc_now(),
        "roots": [str(path.resolve()) for path in args.root],
        "summary": {
            "jsonFilesScanned": scanned,
            "recognizedEvidenceRecords": len(raw_records),
            "songsDiscovered": len(songs),
            "pipelinesComplete": sum(1 for song in songs if song["pipelineComplete"]),
            "unreadableJsonFiles": len(unreadable),
        },
        "songs": songs,
        "unreadableJson": unreadable,
        "truthBoundary": {
            "existingEvidenceDiscovered": True,
            "evidenceFilesModified": False,
            "audioFilesRead": False,
            "audioHashesRecomputed": False,
            "humanListeningPerformed": False,
            "rvcInferenceExecuted": False,
            "voiceConversionProvenByThisReport": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(args.output.resolve(), report)

    if args.text_output:
        lines = ["ECHOES AUDIO PIPELINE STATUS", ""]
        for song in songs:
            lines.append(
                f"{song['displayName']} | current={song['currentStage']} | "
                f"next={song['nextRequiredStage'] or 'DONE'} | selected={song['selectedRvcLabel'] or '-'}"
            )
        lines.extend(["", "This board reads evidence only. It does not read audio or run inference."])
        args.text_output.parent.mkdir(parents=True, exist_ok=True)
        args.text_output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"EchoesAudioPipelineStatus PASS songs={len(songs)} "
        f"complete={report['summary']['pipelinesComplete']} audio-read=false inference=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesAudioPipelineStatus BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
