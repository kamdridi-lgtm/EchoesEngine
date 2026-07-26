#!/usr/bin/env python3
"""Prove deterministic speech timestamps from the pinned Silero VAD model."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

from prove_silero_vad import (
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    SAMPLE_URL,
    SileroVadRunner,
    decode_wav,
    download,
    resample_linear,
    sha256_bytes,
)
from silero_speech_segments import (
    SegmentationConfig,
    canonical_segments_sha256,
    speech_segments_from_probabilities,
    validate_segments,
)

SCHEMA = "echoes.silero-speech-timestamp-proof.v1"
EXPECTED_SAMPLE_SHA256 = "89f17d9c94c4b31eb320f424628bcbc920abaddbee6e2760fd868bfb1d9a2e47"
DIGEST_PLACEHOLDER = "PIN_AFTER_DISCOVERY"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-segments-sha256", default=DIGEST_PLACEHOLDER)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / "official-en.wav"
    proof_path = output_dir / "silero-speech-timestamps.json"

    sample_bytes = download(SAMPLE_URL)
    sample_sha = sha256_bytes(sample_bytes)
    sample_path.write_bytes(sample_bytes)
    samples, original_rate = decode_wav(sample_path)
    samples = resample_linear(samples, original_rate, SAMPLE_RATE)

    runner = SileroVadRunner(args.model.resolve())
    probabilities = runner.run_audio(samples)
    config = SegmentationConfig()
    first = speech_segments_from_probabilities(probabilities, int(samples.size), config)
    second = speech_segments_from_probabilities(probabilities, int(samples.size), config)
    silence_probabilities = runner.run_audio(np.zeros(CHUNK_SAMPLES * 20, dtype=np.float32))
    silence_segments = speech_segments_from_probabilities(
        silence_probabilities, CHUNK_SAMPLES * 20, config
    )

    digest = canonical_segments_sha256(first)
    checks = validate_segments(first, int(samples.size))
    checks.update(
        {
            "timestampsDeterministic": first == second,
            "canonicalDigestDeterministic": digest == canonical_segments_sha256(second),
            "silenceProducesNoSegments": not silence_segments,
            "sampleShaPinned": sample_sha == EXPECTED_SAMPLE_SHA256,
            "probabilitiesFinite": all(math.isfinite(value) for value in probabilities),
            "probabilitiesBounded": all(0.0 <= value <= 1.0 for value in probabilities),
        }
    )

    total_speech_samples = sum(int(segment["durationSamples"]) for segment in first)
    coverage = total_speech_samples / float(samples.size)
    expected_digest = args.expected_segments_sha256.strip().lower()
    pinned = expected_digest != DIGEST_PLACEHOLDER.lower()
    blockers: list[str] = []
    for name, passed in checks.items():
        if not passed:
            blockers.append(name.upper())
    if pinned and digest != expected_digest:
        blockers.append("SEGMENT_DIGEST_MISMATCH")
    if not 0.0 < coverage < 1.0:
        blockers.append("SPEECH_COVERAGE_INVALID")

    passed = pinned and not blockers
    proof = {
        "schema": SCHEMA,
        "status": "PASS" if passed else "DISCOVERY",
        "runtime": {
            "name": "onnxruntime",
            "version": ort.__version__,
            "provider": "CPUExecutionProvider",
        },
        "model": {
            "path": args.model.resolve().as_posix(),
            "sha256": sha256_bytes(args.model.read_bytes()),
            "sizeBytes": args.model.stat().st_size,
            "purpose": "voice_activity_detection",
        },
        "sample": {
            "url": SAMPLE_URL,
            "sha256": sample_sha,
            "originalSampleRate": original_rate,
            "evaluatedSampleRate": SAMPLE_RATE,
            "evaluatedSamples": int(samples.size),
            "durationSeconds": round(samples.size / SAMPLE_RATE, 6),
        },
        "segmentation": {
            "threshold": config.threshold,
            "negativeThreshold": config.negative_threshold,
            "minSpeechMs": config.min_speech_ms,
            "minSilenceMs": config.min_silence_ms,
            "speechPadMs": config.speech_pad_ms,
            "frameSamples": CHUNK_SAMPLES,
            "frameSeconds": CHUNK_SAMPLES / SAMPLE_RATE,
        },
        "summary": {
            "probabilityFrames": len(probabilities),
            "segmentCount": len(first),
            "totalSpeechSamples": total_speech_samples,
            "totalSpeechSeconds": round(total_speech_samples / SAMPLE_RATE, 6),
            "speechCoverage": round(coverage, 9),
            "segmentsSha256": digest,
        },
        "segments": first,
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "voiceActivityProbabilityProven": True,
            "speechTimestampingProven": passed,
            "vocalIsolationProven": False,
            "voiceConversionProven": False,
            "gpuInferenceProven": False,
            "tensorRtInferenceProven": False,
        },
    }
    proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof, separators=(",", ":")))

    if not pinned:
        print(
            "SILERO_TIMESTAMP_DISCOVERY "
            f"segments={len(first)} digest={digest} coverage={coverage:.9f} "
            f"speechSeconds={total_speech_samples / SAMPLE_RATE:.6f}",
            file=sys.stderr,
        )
        return 3
    if blockers:
        print("Silero speech timestamps blocked: " + ",".join(blockers), file=sys.stderr)
        return 2
    print(
        "EchoesSileroSpeechTimestamps PASS "
        f"segments={len(first)} digest={digest} coverage={coverage:.9f} "
        "vocal-isolation=false voice-conversion=false gpu=false tensorrt=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
