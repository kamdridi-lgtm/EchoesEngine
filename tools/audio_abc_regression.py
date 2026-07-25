#!/usr/bin/env python3
"""Objective A/B/C regression proof for the native Echoes audio chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "echoes.audio-abc-proof.v1"


class AudioProofError(RuntimeError):
    pass


@dataclass(frozen=True)
class WaveData:
    path: Path
    audio_format: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    frames: int
    samples: tuple[float, ...]
    file_sha256: str

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate


def read_wave(path: Path) -> WaveData:
    data = path.read_bytes()
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioProofError(f"not a RIFF/WAVE file: {path}")
    cursor = 12
    fmt: tuple[int, int, int, int, int, int] | None = None
    payload: bytes | None = None
    while cursor + 8 <= len(data):
        chunk_id = data[cursor : cursor + 4]
        size = struct.unpack_from("<I", data, cursor + 4)[0]
        start = cursor + 8
        end = start + size
        if end > len(data):
            raise AudioProofError(f"WAV chunk exceeds file size: {path}")
        if chunk_id == b"fmt ":
            if size < 16:
                raise AudioProofError(f"invalid fmt chunk: {path}")
            fmt = struct.unpack_from("<HHIIHH", data, start)
        elif chunk_id == b"data":
            payload = data[start:end]
        cursor = end + (size & 1)
    if fmt is None or payload is None:
        raise AudioProofError(f"missing fmt or data chunk: {path}")
    audio_format, channels, sample_rate, _byte_rate, block_align, bits = fmt
    if channels <= 0 or sample_rate <= 0 or block_align <= 0:
        raise AudioProofError(f"invalid WAV geometry: {path}")
    if len(payload) % block_align:
        raise AudioProofError(f"data chunk is not frame aligned: {path}")
    frames = len(payload) // block_align
    if audio_format == 1 and bits == 16:
        raw = struct.unpack(f"<{len(payload) // 2}h", payload)
        samples = tuple(value / 32768.0 for value in raw)
    elif audio_format == 3 and bits == 32:
        samples = struct.unpack(f"<{len(payload) // 4}f", payload)
    else:
        raise AudioProofError(
            f"unsupported WAV format={audio_format} bits={bits}; expected PCM16 or float32: {path}"
        )
    if len(samples) != frames * channels:
        raise AudioProofError(f"sample count does not match frame geometry: {path}")
    if not samples:
        raise AudioProofError(f"WAV has no samples: {path}")
    return WaveData(
        path=path,
        audio_format=audio_format,
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits,
        frames=frames,
        samples=tuple(float(value) for value in samples),
        file_sha256=hashlib.sha256(data).hexdigest(),
    )


def dbfs(value: float) -> float | None:
    return 20.0 * math.log10(value) if value > 0 else None


def metrics(wave: WaveData) -> dict[str, Any]:
    finite = all(math.isfinite(value) for value in wave.samples)
    if not finite:
        peak = math.inf
        rms = math.inf
        dc = math.inf
    else:
        peak = max(abs(value) for value in wave.samples)
        rms = math.sqrt(sum(value * value for value in wave.samples) / len(wave.samples))
        dc = sum(wave.samples) / len(wave.samples)
    clipped = sum(1 for value in wave.samples if math.isfinite(value) and abs(value) > 1.0 + 1e-6)
    return {
        "path": str(wave.path),
        "audioFormat": wave.audio_format,
        "channels": wave.channels,
        "sampleRate": wave.sample_rate,
        "bitsPerSample": wave.bits_per_sample,
        "frames": wave.frames,
        "sampleCount": len(wave.samples),
        "durationSeconds": wave.duration_seconds,
        "finite": finite,
        "peak": peak,
        "peakDbFs": dbfs(peak) if finite else None,
        "rms": rms,
        "rmsDbFs": dbfs(rms) if finite else None,
        "crestFactorDb": 20.0 * math.log10(peak / rms) if finite and peak > 0 and rms > 0 else None,
        "dcOffset": dc,
        "clippedSamples": clipped,
        "sha256": wave.file_sha256,
        "sizeBytes": wave.path.stat().st_size,
    }


def delta_metrics(left: WaveData, right: WaveData) -> dict[str, float]:
    if len(left.samples) != len(right.samples):
        raise AudioProofError("cannot compare WAV files with different sample counts")
    deltas = [a - b for a, b in zip(left.samples, right.samples)]
    maximum = max(abs(value) for value in deltas)
    rms = math.sqrt(sum(value * value for value in deltas) / len(deltas))
    return {"maxAbsDelta": maximum, "rmsDelta": rms}


def build_proof(input_path: Path, bypass_path: Path, processed_path: Path) -> dict[str, Any]:
    source = read_wave(input_path)
    bypass = read_wave(bypass_path)
    processed = read_wave(processed_path)
    blockers: list[str] = []
    for label, wave in (("BYPASS", bypass), ("PROCESSED", processed)):
        if wave.channels != source.channels:
            blockers.append(f"{label}_CHANNEL_MISMATCH")
        if wave.sample_rate != source.sample_rate:
            blockers.append(f"{label}_SAMPLE_RATE_MISMATCH")
        if wave.frames != source.frames:
            blockers.append(f"{label}_FRAME_COUNT_MISMATCH")
    source_metrics = metrics(source)
    bypass_metrics = metrics(bypass)
    processed_metrics = metrics(processed)
    bypass_delta = delta_metrics(source, bypass) if not any("BYPASS_" in b for b in blockers) else {}
    processed_delta = delta_metrics(bypass, processed) if not any("PROCESSED_" in b for b in blockers) else {}
    if not bypass_metrics["finite"]:
        blockers.append("BYPASS_NON_FINITE")
    if not processed_metrics["finite"]:
        blockers.append("PROCESSED_NON_FINITE")
    if bypass_delta and bypass_delta["maxAbsDelta"] > 1.0 / 32768.0 + 1e-7:
        blockers.append("BYPASS_NOT_TRANSPARENT")
    if processed_delta and processed_delta["rmsDelta"] <= 1e-5:
        blockers.append("PROCESSED_PATH_DID_NOT_CHANGE_AUDIO")
    if processed_metrics["rms"] <= 1e-7:
        blockers.append("PROCESSED_OUTPUT_SILENT")
    if processed_metrics["clippedSamples"] > 0:
        blockers.append("PROCESSED_OUTPUT_CLIPPED")
    if abs(processed_metrics["dcOffset"]) > 0.02:
        blockers.append("PROCESSED_DC_OFFSET_EXCESSIVE")
    if bypass.file_sha256 == processed.file_sha256:
        blockers.append("BYPASS_AND_PROCESSED_HASH_MATCH")
    return {
        "schema": SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "source": source_metrics,
        "bypass": bypass_metrics,
        "processed": processed_metrics,
        "comparisons": {
            "sourceToBypass": bypass_delta,
            "bypassToProcessed": processed_delta,
        },
        "blockers": blockers,
        "objectiveProof": {
            "geometryPreserved": not any(
                marker in blocker
                for blocker in blockers
                for marker in ("CHANNEL_MISMATCH", "SAMPLE_RATE_MISMATCH", "FRAME_COUNT_MISMATCH")
            ),
            "bypassTransparent": "BYPASS_NOT_TRANSPARENT" not in blockers,
            "processedSignalChanged": "PROCESSED_PATH_DID_NOT_CHANGE_AUDIO" not in blockers,
            "processedFinite": "PROCESSED_NON_FINITE" not in blockers,
            "processedUnclipped": "PROCESSED_OUTPUT_CLIPPED" not in blockers,
        },
        "secretsPersisted": False,
    }


def write_float_wave(path: Path, channels: int, sample_rate: int, samples: list[float]) -> None:
    if len(samples) % channels:
        raise ValueError("sample list must be frame aligned")
    payload = struct.pack(f"<{len(samples)}f", *samples)
    block_align = channels * 4
    fmt = struct.pack("<HHIIHH", 3, channels, sample_rate, sample_rate * block_align, block_align, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-audio-abc-") as temporary:
        root = Path(temporary)
        source = root / "source.wav"
        bypass = root / "bypass.wav"
        processed = root / "processed.wav"
        samples = [0.1, -0.1, 0.25, -0.25, 0.4, -0.4, 0.05, -0.05]
        write_float_wave(source, 2, 44100, samples)
        write_float_wave(bypass, 2, 44100, samples)
        write_float_wave(processed, 2, 44100, [value * 0.8 for value in samples])
        proof = build_proof(source, bypass, processed)
        assert proof["status"] == "PASS"
        assert proof["objectiveProof"]["bypassTransparent"] is True
        assert proof["objectiveProof"]["processedSignalChanged"] is True
        write_float_wave(processed, 2, 44100, samples)
        blocked = build_proof(source, bypass, processed)
        assert blocked["status"] == "BLOCKED"
        assert "PROCESSED_PATH_DID_NOT_CHANGE_AUDIO" in blocked["blockers"]
    print("AudioABCRegression PASS transparent-bypass=proven processed-change=proven unchanged=blocked")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--bypass", type=Path)
    parser.add_argument("--processed", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.input or not args.bypass or not args.processed:
        parser.error("--input, --bypass, and --processed are required")
    try:
        proof = build_proof(args.input, args.bypass, args.processed)
    except (OSError, AudioProofError, ValueError) as error:
        proof = {
            "schema": SCHEMA,
            "status": "BLOCKED",
            "failureClass": "AUDIO_PROOF_INPUT_INVALID",
            "error": str(error),
            "secretsPersisted": False,
        }
    rendered = json.dumps(proof, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if proof.get("status") == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
