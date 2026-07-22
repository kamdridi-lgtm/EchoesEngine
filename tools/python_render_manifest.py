#!/usr/bin/env python3
"""Generate an Echoes render manifest without requiring a local C++ compiler.

This is the canonical local-proof fallback when Visual Studio Build Tools are not
installed. It consumes the same section CSV fixture used by RenderManifestCli and
writes ``echoes.render-manifest.v1`` for the existing render workers.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any


ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_PROMPT = (
    "A cinematic industrial rock performance in a rain-soaked megacity at night, "
    "dramatic amber and deep red lighting, realistic camera motion, coherent movement, "
    "high-detail atmosphere, premium music-video composition"
)


def stable_seed(job_id: str, shot_id: str, base_seed: int, index: int) -> int:
    value = 2166136261
    payload = f"{job_id}|{shot_id}|{base_seed}|{index}".encode("utf-8")
    for byte in payload:
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def parse_bool(raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid beat value: {raw}")


def camera_name(energy: float, tempo: float) -> str:
    if energy >= 0.82:
        return "Tracking"
    if tempo >= 120.0:
        return "Orbit"
    if energy <= 0.35:
        return "SlowPush"
    return "PullBack"


def section_prompt(base_prompt: str, section_id: str, energy: float, tempo: float, beat: bool) -> str:
    descriptors: list[str] = [base_prompt, f"section {section_id}"]
    if energy >= 0.82:
        descriptors.append("intense kinetic performance energy")
    elif energy >= 0.6:
        descriptors.append("controlled dramatic performance energy")
    else:
        descriptors.append("slow atmospheric tension")
    if tempo >= 120.0:
        descriptors.append("driving rhythm and purposeful camera movement")
    else:
        descriptors.append("measured cinematic pacing")
    if beat:
        descriptors.append("motion synchronized to the musical pulse")
    return ", ".join(descriptors)


def read_sections(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(line for line in handle if line.strip() and not line.lstrip().startswith("#"))
        for line_number, columns in enumerate(reader, start=1):
            if len(columns) != 9:
                raise ValueError(f"section row {line_number} must have 9 columns")
            section_id = columns[0].strip()
            if not ID_PATTERN.fullmatch(section_id):
                raise ValueError(f"invalid section id: {section_id}")
            start = float(columns[1])
            end = float(columns[2])
            bass = float(columns[3])
            mid = float(columns[4])
            treble = float(columns[5])
            energy = float(columns[6])
            tempo = float(columns[7])
            beat = parse_bool(columns[8])
            numeric = (start, end, bass, mid, treble, energy, tempo)
            if not all(math.isfinite(value) for value in numeric):
                raise ValueError(f"section {section_id} contains a non-finite value")
            if start < 0 or end <= start:
                raise ValueError(f"section {section_id} has an invalid time range")
            if not all(0.0 <= value <= 1.0 for value in (bass, mid, treble, energy)):
                raise ValueError(f"section {section_id} spectral/energy values must be between 0 and 1")
            if tempo <= 0:
                raise ValueError(f"section {section_id} tempo must be positive")
            rows.append(
                {
                    "id": section_id,
                    "start": start,
                    "end": end,
                    "energy": energy,
                    "tempo": tempo,
                    "beat": beat,
                }
            )
    if not rows:
        raise ValueError("sections CSV contains no data rows")
    return rows


def build_manifest(
    sections: list[dict[str, Any]],
    *,
    job_id: str,
    base_seed: int,
    output_directory: str,
    prompt: str,
) -> dict[str, Any]:
    if not ID_PATTERN.fullmatch(job_id):
        raise ValueError("job id must contain only letters, digits, dot, underscore, or hyphen")
    if base_seed < 0 or base_seed > 0xFFFFFFFF:
        raise ValueError("seed must fit in an unsigned 32-bit integer")
    output_directory = output_directory.strip().replace("\\", "/").strip("/") or "clips"
    if output_directory.startswith("/") or ".." in output_directory.split("/"):
        raise ValueError("output directory must be a safe relative path")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("base prompt must not be empty")

    tasks: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        duration = section["end"] - section["start"]
        tasks.append(
            {
                "id": f"{job_id}-task-{index + 1}",
                "shotId": section["id"],
                "startSeconds": round(section["start"], 3),
                "durationSeconds": round(duration, 3),
                "seed": stable_seed(job_id, section["id"], base_seed, index),
                "camera": camera_name(section["energy"], section["tempo"]),
                "transition": "fade_in" if index == 0 else ("beat_cut" if section["beat"] else "cinematic_dissolve"),
                "prompt": section_prompt(prompt, section["id"], section["energy"], section["tempo"], section["beat"]),
                "continuity": {
                    "subjectId": "echoes-proof-subject",
                    "styleId": "echoes-industrial-cinema-v1",
                    "referenceAsset": "",
                    "strength": 0.75,
                },
                "outputFile": f"{output_directory}/{section['id']}.mp4",
            }
        )

    return {
        "schema": "echoes.render-manifest.v1",
        "jobId": job_id,
        "durationSeconds": round(max(section["end"] for section in sections), 3),
        "generator": "python-render-manifest-v1",
        "tasks": tasks,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-python-manifest-") as temp:
        root = Path(temp)
        fixture = root / "sections.csv"
        output = root / "manifest.json"
        fixture.write_text("proof,0.0,4.0,0.7,0.6,0.4,0.86,126,true\n", encoding="utf-8")
        manifest = build_manifest(
            read_sections(fixture),
            job_id="self-test",
            base_seed=7331,
            output_directory="clips",
            prompt=DEFAULT_PROMPT,
        )
        write_manifest(output, manifest)
        parsed = json.loads(output.read_text(encoding="utf-8"))
        assert parsed["schema"] == "echoes.render-manifest.v1"
        assert parsed["durationSeconds"] == 4.0
        assert len(parsed["tasks"]) == 1
        assert parsed["tasks"][0]["outputFile"] == "clips/proof.mp4"
        assert parsed["tasks"][0]["camera"] == "Tracking"
    print("PythonRenderManifest self-test PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sections_csv", nargs="?", type=Path)
    parser.add_argument("output_json", nargs="?", type=Path)
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("seed", nargs="?", type=int, default=1337)
    parser.add_argument("output_directory", nargs="?", default="clips")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.sections_csv is None or args.output_json is None or args.job_id is None:
        parser.error("sections_csv, output_json, and job_id are required unless --self-test is used")

    manifest = build_manifest(
        read_sections(args.sections_csv),
        job_id=args.job_id,
        base_seed=args.seed,
        output_directory=args.output_directory,
        prompt=args.prompt,
    )
    write_manifest(args.output_json, manifest)
    print(
        f"PythonRenderManifest PASS job={args.job_id} tasks={len(manifest['tasks'])} "
        f"duration={manifest['durationSeconds']} output={args.output_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
