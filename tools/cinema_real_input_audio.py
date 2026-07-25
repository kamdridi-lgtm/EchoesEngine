#!/usr/bin/env python3
"""Select and prepare genuine input audio for the Echoes Cinema P0 proof.

The production path never synthesizes fallback audio. It discovers a non-empty
user/project audio file, transcodes the first proof window with FFmpeg, validates
the resulting WAV, and records source/output hashes for the evidence bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "echoes.cinema-real-input-audio.v1"
SUPPORTED_EXTENSIONS = frozenset({".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma"})
PRIORITY_TOKENS = ("p0", "war machines", "too fast too young", "echoes", "kam dridi")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_audio_file(path: Path) -> bool:
    try:
        return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and path.stat().st_size > 44
    except OSError:
        return False


def _priority(path: Path) -> tuple[int, float, str]:
    normalized = path.stem.lower().replace("_", " ").replace("-", " ")
    token_rank = next((index for index, token in enumerate(PRIORITY_TOKENS) if token in normalized), len(PRIORITY_TOKENS))
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return token_rank, -modified, str(path).lower()


def discover_real_input(workspace: Path, explicit_path: str | None = None) -> tuple[Path | None, str]:
    """Return one deterministic input source and how it was selected."""

    if explicit_path:
        explicit = Path(explicit_path).expanduser()
        if not explicit.is_absolute():
            explicit = workspace / explicit
        explicit = explicit.resolve()
        if not _valid_audio_file(explicit):
            raise RuntimeError(f"Configured P0 audio is missing, empty, or unsupported: {explicit}")
        return explicit, "ECHOES_CINEMA_P0_AUDIO"

    input_root = workspace / "input"
    input_root.mkdir(parents=True, exist_ok=True)
    candidates = sorted((path for path in input_root.rglob("*") if _valid_audio_file(path)), key=_priority)
    return (candidates[0], "workspace-input-priority") if candidates else (None, "workspace-input-empty")


def inspect_wav(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"Prepared proof audio is not a readable WAV: {error}") from error
    duration = frames / sample_rate if sample_rate else 0.0
    if channels != 2:
        raise RuntimeError(f"Prepared proof audio must be stereo; channels={channels}")
    if sample_width != 2:
        raise RuntimeError(f"Prepared proof audio must be 16-bit PCM; sampleWidth={sample_width}")
    if sample_rate != 44_100:
        raise RuntimeError(f"Prepared proof audio must be 44100 Hz; sampleRate={sample_rate}")
    if duration < 3.8 or duration > 4.2:
        raise RuntimeError(f"Prepared proof audio duration must be approximately 4 seconds; duration={duration:.3f}")
    return {
        "channels": channels,
        "sampleWidthBytes": sample_width,
        "sampleRate": sample_rate,
        "frameCount": frames,
        "durationSeconds": round(duration, 6),
    }


def _cached_evidence_valid(evidence: dict[str, Any] | None, source: Path, output_wav: Path) -> bool:
    if not evidence or evidence.get("status") != "PASS" or evidence.get("generatedByAutopilot") is not False:
        return False
    if not output_wav.is_file() or output_wav.stat().st_size <= 44:
        return False
    try:
        return (
            evidence.get("sourcePath") == str(source)
            and evidence.get("sourceSha256") == sha256_file(source)
            and evidence.get("outputSha256") == sha256_file(output_wav)
        )
    except OSError:
        return False


def prepare_real_input(
    source: Path,
    output_wav: Path,
    evidence_path: Path,
    *,
    selection_method: str,
    proof_duration_seconds: float = 4.0,
    source_classification: str = "DISCOVERED_PROJECT_AUDIO",
) -> dict[str, Any]:
    """Transcode and hash a real project input. No synthetic fallback exists."""

    source = source.resolve()
    if not _valid_audio_file(source):
        raise RuntimeError(f"Selected P0 input audio is missing, empty, or unsupported: {source}")
    existing = load_json(evidence_path)
    if _cached_evidence_valid(existing, source, output_wav):
        return dict(existing or {})

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to prepare the real P0 input audio")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_wav.with_name(output_wav.name + f".{os.getpid()}.tmp.wav")
    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-t",
        f"{proof_duration_seconds:.3f}",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(temporary),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"FFmpeg could not prepare the selected P0 input audio: {detail}")
    media = inspect_wav(temporary)
    os.replace(temporary, output_wav)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "timestampUtc": utc_now(),
        "status": "PASS",
        "truthStatus": "REAL_INPUT",
        "sourceClassification": source_classification,
        "selectionMethod": selection_method,
        "generatedByAutopilot": False,
        "syntheticFallbackAllowed": False,
        "sourcePath": str(source),
        "sourceFileName": source.name,
        "sourceSizeBytes": source.stat().st_size,
        "sourceSha256": sha256_file(source),
        "outputPath": str(output_wav.resolve()),
        "outputSizeBytes": output_wav.stat().st_size,
        "outputSha256": sha256_file(output_wav),
        "proofDurationSeconds": proof_duration_seconds,
        "media": media,
        "systemDriveWritesAllowed": False,
    }
    atomic_json(evidence_path, payload)
    return payload


def _write_mock_test_wav(path: Path, seconds: float = 5.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44_100
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        silence = b"\x00\x00" * min(frames, sample_rate)
        remaining = frames
        while remaining:
            count = min(remaining, sample_rate)
            output.writeframesraw(silence[: count * 2])
            remaining -= count


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-real-input-audio-") as temporary:
        workspace = Path(temporary) / "D-drive-simulation" / "EchoesCinema"
        source = workspace / "input" / "War Machines P0.wav"
        _write_mock_test_wav(source)
        selected, method = discover_real_input(workspace)
        assert selected == source.resolve()
        output = workspace / "proofs" / "first-real-ai-clip" / "proof-audio.wav"
        evidence_path = output.with_name("proof-audio-source.json")
        evidence = prepare_real_input(
            selected,
            output,
            evidence_path,
            selection_method=method,
            source_classification="MOCK_TEST_FIXTURE",
        )
        assert evidence["status"] == "PASS"
        assert evidence["generatedByAutopilot"] is False
        assert evidence["sourceClassification"] == "MOCK_TEST_FIXTURE"
        assert len(evidence["sourceSha256"]) == 64
        assert len(evidence["outputSha256"]) == 64
        assert output.is_file() and evidence_path.is_file()
        cached = prepare_real_input(
            selected,
            output,
            evidence_path,
            selection_method=method,
            source_classification="MOCK_TEST_FIXTURE",
        )
        assert cached["outputSha256"] == evidence["outputSha256"]
        missing_workspace = Path(temporary) / "empty" / "EchoesCinema"
        missing, missing_method = discover_real_input(missing_workspace)
        assert missing is None and missing_method == "workspace-input-empty"
    print("CinemaRealInputAudio PASS selection=deterministic fallback=forbidden hashes=present fixture=MOCK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    parser.error("Only --self-test is supported; production use is through cinema_p0_autopilot.py")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
