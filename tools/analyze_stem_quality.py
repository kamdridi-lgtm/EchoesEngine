#!/usr/bin/env python3
"""Measure structural and signal-level quality of an Echoes four-stem separation.

This module deliberately does not claim subjective isolation quality or musical
usability. It proves decodability, geometry, finite samples, hashes, signal
statistics and source-vs-sum reconstruction measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

EXPECTED_STEMS = ("vocals", "drums", "bass", "other")
SCHEMA = "echoes.stem-technical-quality.v1"
SAMPLE_RATE = 44100
CHANNELS = 2
CHUNK_FRAMES = 4096


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dbfs(value: float) -> float | None:
    if not math.isfinite(value) or value <= 0.0:
        return None
    return round(20.0 * math.log10(value), 6)


class SignalAccumulator:
    def __init__(self) -> None:
        self.samples = 0
        self.sum = 0.0
        self.sum_squares = 0.0
        self.peak = 0.0
        self.non_finite = 0
        self.clipped = 0

    def update(self, values: np.ndarray) -> None:
        if values.size == 0:
            return
        finite_mask = np.isfinite(values)
        self.non_finite += int(values.size - np.count_nonzero(finite_mask))
        finite = values[finite_mask].astype(np.float64, copy=False)
        self.samples += int(values.size)
        if finite.size == 0:
            return
        self.sum += float(np.sum(finite, dtype=np.float64))
        self.sum_squares += float(np.dot(finite, finite))
        self.peak = max(self.peak, float(np.max(np.abs(finite))))
        self.clipped += int(np.count_nonzero(np.abs(finite) >= 0.999999))

    def render(self) -> dict[str, Any]:
        finite_samples = max(0, self.samples - self.non_finite)
        mean = self.sum / finite_samples if finite_samples else 0.0
        rms = math.sqrt(max(0.0, self.sum_squares / finite_samples)) if finite_samples else 0.0
        return {
            "interleavedSamples": self.samples,
            "frames": self.samples // CHANNELS,
            "durationSeconds": round((self.samples // CHANNELS) / SAMPLE_RATE, 6),
            "finiteSamples": finite_samples,
            "nonFiniteSamples": self.non_finite,
            "peakLinear": round(self.peak, 9),
            "peakDbfs": dbfs(self.peak),
            "rmsLinear": round(rms, 9),
            "rmsDbfs": dbfs(rms),
            "dcOffset": round(mean, 12),
            "clippedSamples": self.clipped,
            "clippedFraction": round(self.clipped / finite_samples, 12) if finite_samples else 0.0,
        }


class Decoder:
    def __init__(self, ffmpeg: str, path: Path) -> None:
        self.path = path
        self.process = subprocess.Popen(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                str(CHANNELS),
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "f32le",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self.process.stdout is None or self.process.stderr is None:
            raise RuntimeError(f"Unable to open FFmpeg pipes for {path}")

    def read(self, byte_count: int) -> bytes:
        return self.process.stdout.read(byte_count)

    def finish(self) -> tuple[int, str]:
        stderr = self.process.stderr.read().decode("utf-8", errors="replace")
        return self.process.wait(), stderr.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--stems-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declare-user-song", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    stems_dir = args.stems_dir.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise RuntimeError(f"Source audio is missing: {source}")
    source_sha = sha256_file(source)
    if source_sha != args.expected_source_sha256.lower():
        raise RuntimeError(
            f"INPUT_SHA256_MISMATCH expected={args.expected_source_sha256.lower()} actual={source_sha}"
        )

    paths = {"source": source}
    for name in EXPECTED_STEMS:
        stem = stems_dir / f"{name}.wav"
        if not stem.is_file() or stem.stat().st_size <= 44:
            raise RuntimeError(f"Expected stem is missing or empty: {stem}")
        paths[name] = stem

    decoders = {name: Decoder(args.ffmpeg, path) for name, path in paths.items()}
    accumulators = {name: SignalAccumulator() for name in paths}
    residual = SignalAccumulator()
    summed = SignalAccumulator()
    cross_xy = 0.0
    source_energy = 0.0
    sum_energy = 0.0
    compared_samples = 0
    stream_samples = {name: 0 for name in paths}
    chunk_bytes = CHUNK_FRAMES * CHANNELS * 4

    while True:
        raw = {name: decoder.read(chunk_bytes) for name, decoder in decoders.items()}
        if all(not value for value in raw.values()):
            break
        arrays: dict[str, np.ndarray] = {}
        for name, payload in raw.items():
            usable = len(payload) - (len(payload) % 4)
            values = np.frombuffer(payload[:usable], dtype="<f4") if usable else np.empty(0, dtype=np.float32)
            arrays[name] = values
            stream_samples[name] += int(values.size)
            accumulators[name].update(values)

        common = min(values.size for values in arrays.values())
        common -= common % CHANNELS
        if common <= 0:
            continue
        source_values = arrays["source"][:common].astype(np.float64, copy=False)
        stem_sum = np.zeros(common, dtype=np.float64)
        for name in EXPECTED_STEMS:
            stem_sum += arrays[name][:common]
        difference = source_values - stem_sum
        summed.update(stem_sum.astype(np.float32, copy=False))
        residual.update(difference.astype(np.float32, copy=False))
        cross_xy += float(np.dot(source_values, stem_sum))
        source_energy += float(np.dot(source_values, source_values))
        sum_energy += float(np.dot(stem_sum, stem_sum))
        compared_samples += int(common)

    decoder_results: dict[str, Any] = {}
    decoder_failures: list[str] = []
    for name, decoder in decoders.items():
        exit_code, error = decoder.finish()
        decoder_results[name] = {"exitCode": exit_code, "stderr": error}
        if exit_code != 0:
            decoder_failures.append(f"FFMPEG_DECODE_FAILED:{name}:{exit_code}")

    signals = {name: accumulator.render() for name, accumulator in accumulators.items()}
    source_rms = float(signals["source"]["rmsLinear"] or 0.0)
    for name in EXPECTED_STEMS:
        stem_rms = float(signals[name]["rmsLinear"] or 0.0)
        signals[name]["energyDbRelativeToSource"] = (
            round(20.0 * math.log10(stem_rms / source_rms), 6)
            if source_rms > 0.0 and stem_rms > 0.0
            else None
        )

    residual_stats = residual.render()
    summed_stats = summed.render()
    residual_rms = float(residual_stats["rmsLinear"] or 0.0)
    residual_relative_db = (
        round(20.0 * math.log10(residual_rms / source_rms), 6)
        if source_rms > 0.0 and residual_rms > 0.0
        else None
    )
    correlation = (
        cross_xy / math.sqrt(source_energy * sum_energy)
        if source_energy > 0.0 and sum_energy > 0.0
        else 0.0
    )

    blockers = list(decoder_failures)
    if source_rms <= 0.0:
        blockers.append("SOURCE_SIGNAL_EMPTY")
    for name, signal in signals.items():
        if signal["interleavedSamples"] <= 0:
            blockers.append(f"EMPTY_DECODE:{name}")
        if signal["nonFiniteSamples"] != 0:
            blockers.append(f"NON_FINITE_SAMPLES:{name}")
    maximum_samples = max(stream_samples.values())
    minimum_samples = min(stream_samples.values())
    tolerance_samples = int(0.25 * SAMPLE_RATE * CHANNELS)
    if maximum_samples - minimum_samples > tolerance_samples:
        blockers.append("STREAM_DURATION_DRIFT_EXCEEDED")
    if compared_samples <= 0:
        blockers.append("NO_OVERLAPPING_SAMPLES")

    advisories: list[str] = []
    for name in EXPECTED_STEMS:
        signal = signals[name]
        if float(signal["rmsLinear"] or 0.0) <= 1e-6:
            advisories.append(f"VERY_LOW_STEM_ENERGY:{name}")
        if float(signal["clippedFraction"] or 0.0) > 0.001:
            advisories.append(f"STEM_CLIPPING_REVIEW:{name}")
    if residual_relative_db is not None and residual_relative_db > -10.0:
        advisories.append("RECONSTRUCTION_RESIDUAL_REVIEW")
    if correlation < 0.8:
        advisories.append("RECONSTRUCTION_CORRELATION_REVIEW")

    report = {
        "schema": SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "advisoryStatus": "PASS" if not advisories else "REVIEW",
        "finishedAtUtc": utc_now(),
        "source": {
            "path": str(source),
            "name": source.name,
            "sha256": source_sha,
            "sizeBytes": source.stat().st_size,
            "declaredUserSong": bool(args.declare_user_song),
        },
        "format": {
            "analysisSampleRate": SAMPLE_RATE,
            "analysisChannels": CHANNELS,
            "analysisPrecision": "float32 decoded; float64 accumulation",
        },
        "signals": signals,
        "reconstruction": {
            "comparedInterleavedSamples": compared_samples,
            "comparedFrames": compared_samples // CHANNELS,
            "durationSeconds": round((compared_samples // CHANNELS) / SAMPLE_RATE, 6),
            "summedStems": summed_stats,
            "residual": residual_stats,
            "residualRmsDbRelativeToSource": residual_relative_db,
            "sourceToStemSumCorrelation": round(correlation, 9),
            "exactReconstructionClaimed": False,
        },
        "decoders": decoder_results,
        "checks": {
            "sourceShaVerified": source_sha == args.expected_source_sha256.lower(),
            "allFiveStreamsDecoded": not decoder_failures,
            "allSignalsFinite": all(item["nonFiniteSamples"] == 0 for item in signals.values()),
            "allSignalsNonEmpty": all(item["interleavedSamples"] > 0 for item in signals.values()),
            "durationAgreementWithin250Ms": maximum_samples - minimum_samples <= tolerance_samples,
            "fourStemFilesPresent": all((stems_dir / f"{name}.wav").is_file() for name in EXPECTED_STEMS),
        },
        "blockers": blockers,
        "advisories": advisories,
        "truthBoundary": {
            "technicalStemQcExecuted": True,
            "technicalStemQcProven": not blockers,
            "vocalStemGenerated": True,
            "fourStemFilesGenerated": True,
            "subjectiveIsolationQualityProven": False,
            "vocalIsolationQualityProven": False,
            "musicalUsabilityProven": False,
            "acapellaReady": False,
            "instrumentalMixReady": False,
            "voiceConversionProven": False,
            "humanListeningReviewCompleted": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"EchoesStemTechnicalQuality {report['status']} advisory={report['advisoryStatus']} "
        f"correlation={correlation:.6f} residualRelativeDb={residual_relative_db} output={output}"
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes stem technical quality failed: {error}", file=sys.stderr)
        raise SystemExit(2)
