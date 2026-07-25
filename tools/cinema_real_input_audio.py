#!/usr/bin/env python3
"""Select and prepare genuine input audio for the Echoes Cinema P0 proof.

The production path never synthesizes fallback audio. It first checks the
canonical Echoes Cinema input directory, then performs a bounded, deterministic
search of normal user/project audio locations. Generated proofs, model caches,
renders, jobs, temporary files, and other unsafe roots are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "echoes.cinema-real-input-audio.v1"
SUPPORTED_EXTENSIONS = frozenset({".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma"})
PRIORITY_TOKENS = (
    "p0",
    "you are the one",
    "war machines",
    "too fast too young",
    "echoes",
    "kam dridi",
)
TARGET_SAMPLE_RATE = 44_100
TARGET_CHANNELS = 2
TARGET_SAMPLE_WIDTH = 2
AUTO_SEARCH_MIN_BYTES = 64 * 1024
AUTO_SEARCH_MAX_DEPTH = 5
AUTO_SEARCH_MAX_FILES = 25_000
SKIP_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".venv-cinema",
        "__pycache__",
        "node_modules",
        "models",
        "model",
        "checkpoints",
        "cache",
        "caches",
        "runtime",
        "proofs",
        "jobs",
        "render-output",
        "renders",
        "outputs",
        "temp",
        "tmp",
        "backups",
        "logs",
        "dist",
        "build",
    }
)
GENERATED_FILE_TOKENS = (
    "proof-audio",
    "first-real-ai-clip",
    "synthetic-proof",
    "mock-fixture",
    "test-tone",
)


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


def _normalized_key(path: Path) -> str:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return os.path.normcase(str(resolved))


def _deduplicate_roots(entries: Iterable[tuple[Path, str]]) -> list[tuple[Path, str]]:
    seen: set[str] = set()
    result: list[tuple[Path, str]] = []
    for root, label in entries:
        key = _normalized_key(root)
        if key in seen:
            continue
        seen.add(key)
        result.append((Path(key), label))
    return result


def default_search_roots(workspace: Path) -> list[tuple[Path, str]]:
    """Return bounded fallback roots without requiring the user to move a file."""

    entries: list[tuple[Path, str]] = []
    configured = os.getenv("ECHOES_CINEMA_P0_SEARCH_ROOTS", "")
    for index, raw in enumerate(configured.replace("\r", "\n").replace("\n", ";").split(";"), start=1):
        value = raw.strip().strip('"')
        if value:
            entries.append((Path(value).expanduser(), f"configured-search-root-{index}"))

    home_candidates = [Path.home()]
    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        home_candidates.append(Path(user_profile))
    for home in home_candidates:
        entries.extend(
            [
                (home / "Downloads", "auto-search-user-downloads"),
                (home / "Music", "auto-search-user-music"),
                (home / "Desktop", "auto-search-user-desktop"),
            ]
        )

    project_parent = workspace.resolve().parent
    entries.extend(
        [
            (project_parent / "Music", "auto-search-project-music"),
            (project_parent / "Audio", "auto-search-project-audio"),
            (project_parent / "EchoesEngine", "auto-search-echoes-engine"),
        ]
    )

    drive_root = Path(workspace.anchor) if workspace.anchor else None
    if drive_root:
        entries.extend(
            [
                (drive_root / "Music", "auto-search-drive-music"),
                (drive_root / "Audio", "auto-search-drive-audio"),
            ]
        )

    return _deduplicate_roots(entries)


def _candidate_is_excluded(path: Path, workspace: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts.intersection(SKIP_DIRECTORY_NAMES):
        return True
    lowered_name = path.name.lower()
    if any(token in lowered_name for token in GENERATED_FILE_TOKENS):
        return True
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except (OSError, ValueError):
        return False
    return bool({part.lower() for part in relative.parts}.intersection(SKIP_DIRECTORY_NAMES))


def _bounded_audio_candidates(root: Path, workspace: Path) -> list[Path]:
    """Search a root with strict depth/file limits and aggressive cache pruning."""

    try:
        resolved_root = root.expanduser().resolve()
    except OSError:
        return []
    if not resolved_root.is_dir():
        return []

    candidates: list[Path] = []
    scanned_files = 0
    for current, directory_names, file_names in os.walk(resolved_root, topdown=True, onerror=lambda _error: None):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(resolved_root).parts)
        except ValueError:
            continue
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name.lower() not in SKIP_DIRECTORY_NAMES and not name.startswith(".")
        )
        if depth >= AUTO_SEARCH_MAX_DEPTH:
            directory_names[:] = []

        for file_name in sorted(file_names):
            scanned_files += 1
            if scanned_files > AUTO_SEARCH_MAX_FILES:
                return candidates
            path = current_path / file_name
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if _candidate_is_excluded(path, workspace):
                continue
            try:
                if path.stat().st_size < AUTO_SEARCH_MIN_BYTES:
                    continue
            except OSError:
                continue
            if _valid_audio_file(path):
                candidates.append(path.resolve())
    return candidates


def discover_real_input(workspace: Path, explicit_path: str | None = None) -> tuple[Path | None, str]:
    """Return one deterministic real input source and how it was selected."""

    workspace = workspace.resolve()
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
    if candidates:
        return candidates[0].resolve(), "workspace-input-priority"

    fallback_candidates: list[tuple[Path, str]] = []
    for root, label in default_search_roots(workspace):
        for path in _bounded_audio_candidates(root, workspace):
            fallback_candidates.append((path, label))
    if not fallback_candidates:
        return None, "automatic-search-empty"
    selected_path, selected_method = min(fallback_candidates, key=lambda item: _priority(item[0]))
    return selected_path.resolve(), selected_method


def inspect_wav(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
            compression = handle.getcomptype()
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"Prepared proof audio is not a readable WAV: {error}") from error
    duration = frames / sample_rate if sample_rate else 0.0
    if compression != "NONE":
        raise RuntimeError(f"Prepared proof audio must be uncompressed PCM; compression={compression}")
    if channels != TARGET_CHANNELS:
        raise RuntimeError(f"Prepared proof audio must be stereo; channels={channels}")
    if sample_width != TARGET_SAMPLE_WIDTH:
        raise RuntimeError(f"Prepared proof audio must be 16-bit PCM; sampleWidth={sample_width}")
    if sample_rate != TARGET_SAMPLE_RATE:
        raise RuntimeError(f"Prepared proof audio must be 44100 Hz; sampleRate={sample_rate}")
    if duration < 3.8 or duration > 4.2:
        raise RuntimeError(f"Prepared proof audio duration must be approximately 4 seconds; duration={duration:.3f}")
    return {
        "channels": channels,
        "sampleWidthBytes": sample_width,
        "sampleRate": sample_rate,
        "frameCount": frames,
        "durationSeconds": round(duration, 6),
        "compression": compression,
    }


def _prepare_compatible_pcm_wav(source: Path, destination: Path, duration_seconds: float) -> bool:
    """Prepare a compatible PCM WAV without FFmpeg; return False if conversion is required."""

    if source.suffix.lower() != ".wav":
        return False
    try:
        with wave.open(str(source), "rb") as input_wav:
            channels = input_wav.getnchannels()
            sample_width = input_wav.getsampwidth()
            sample_rate = input_wav.getframerate()
            compression = input_wav.getcomptype()
            if (
                channels not in {1, 2}
                or sample_width != TARGET_SAMPLE_WIDTH
                or sample_rate != TARGET_SAMPLE_RATE
                or compression != "NONE"
            ):
                return False
            required_frames = int(round(duration_seconds * sample_rate))
            if input_wav.getnframes() < int(3.8 * sample_rate):
                raise RuntimeError("Selected P0 WAV is shorter than the required proof window")
            frames = input_wav.readframes(required_frames)
    except wave.Error:
        return False

    if channels == 1:
        stereo = bytearray(len(frames) * 2)
        write_offset = 0
        for (sample,) in struct.iter_unpack("<h", frames):
            struct.pack_into("<hh", stereo, write_offset, sample, sample)
            write_offset += 4
        output_frames = bytes(stereo)
    else:
        output_frames = frames

    with wave.open(str(destination), "wb") as output_wav:
        output_wav.setnchannels(TARGET_CHANNELS)
        output_wav.setsampwidth(TARGET_SAMPLE_WIDTH)
        output_wav.setframerate(TARGET_SAMPLE_RATE)
        output_wav.writeframes(output_frames)
    return True


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


def _source_classification(selection_method: str) -> str:
    if selection_method == "ECHOES_CINEMA_P0_AUDIO":
        return "EXPLICIT_USER_AUDIO"
    if selection_method.startswith("auto-search") or selection_method.startswith("configured-search-root"):
        return "AUTO_DISCOVERED_USER_AUDIO"
    return "DISCOVERED_PROJECT_AUDIO"


def prepare_real_input(
    source: Path,
    output_wav: Path,
    evidence_path: Path,
    *,
    selection_method: str,
    proof_duration_seconds: float = 4.0,
    source_classification: str | None = None,
) -> dict[str, Any]:
    """Prepare and hash a real project input. No synthetic fallback exists."""

    source = source.resolve()
    if not _valid_audio_file(source):
        raise RuntimeError(f"Selected P0 input audio is missing, empty, or unsupported: {source}")
    existing = load_json(evidence_path)
    if _cached_evidence_valid(existing, source, output_wav):
        return dict(existing or {})

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_wav.with_name(output_wav.name + f".{os.getpid()}.tmp.wav")
    preparation_backend = "PYTHON_PCM_WAV"
    prepared = _prepare_compatible_pcm_wav(source, temporary, proof_duration_seconds)
    if not prepared:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "Selected audio requires FFmpeg conversion, but FFmpeg is not available. "
                f"Source: {source}"
            )
        preparation_backend = "FFMPEG"
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
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE),
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
        "sourceClassification": source_classification or _source_classification(selection_method),
        "selectionMethod": selection_method,
        "preparationBackend": preparation_backend,
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
    frames = int(TARGET_SAMPLE_RATE * seconds)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(TARGET_CHANNELS)
        output.setsampwidth(TARGET_SAMPLE_WIDTH)
        output.setframerate(TARGET_SAMPLE_RATE)
        block = b"\x00\x00\x00\x00" * min(frames, TARGET_SAMPLE_RATE)
        remaining = frames
        while remaining:
            count = min(remaining, TARGET_SAMPLE_RATE)
            output.writeframesraw(block[: count * 4])
            remaining -= count


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-real-input-audio-") as temporary:
        root = Path(temporary)
        workspace = root / "D-drive-simulation" / "EchoesCinema"
        source = workspace / "input" / "War Machines P0.wav"
        _write_mock_test_wav(source)
        selected, method = discover_real_input(workspace)
        assert selected == source.resolve()
        assert method == "workspace-input-priority"

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
        assert evidence["preparationBackend"] == "PYTHON_PCM_WAV"
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

        source.unlink()
        downloads = root / "User" / "Downloads"
        auto_source = downloads / "You Are the One.wav"
        _write_mock_test_wav(auto_source)
        old_roots = os.environ.get("ECHOES_CINEMA_P0_SEARCH_ROOTS")
        os.environ["ECHOES_CINEMA_P0_SEARCH_ROOTS"] = str(downloads)
        try:
            selected, method = discover_real_input(workspace)
        finally:
            if old_roots is None:
                os.environ.pop("ECHOES_CINEMA_P0_SEARCH_ROOTS", None)
            else:
                os.environ["ECHOES_CINEMA_P0_SEARCH_ROOTS"] = old_roots
        assert selected == auto_source.resolve()
        assert method == "configured-search-root-1"
        auto_evidence = prepare_real_input(
            selected,
            workspace / "proofs" / "auto" / "proof-audio.wav",
            workspace / "proofs" / "auto" / "proof-audio-source.json",
            selection_method=method,
        )
        assert auto_evidence["sourceClassification"] == "AUTO_DISCOVERED_USER_AUDIO"

        excluded_source = workspace / "proofs" / "first-real-ai-clip" / "War Machines P0.wav"
        _write_mock_test_wav(excluded_source)
        old_roots = os.environ.get("ECHOES_CINEMA_P0_SEARCH_ROOTS")
        os.environ["ECHOES_CINEMA_P0_SEARCH_ROOTS"] = str(workspace)
        auto_source.unlink()
        try:
            missing, missing_method = discover_real_input(workspace)
        finally:
            if old_roots is None:
                os.environ.pop("ECHOES_CINEMA_P0_SEARCH_ROOTS", None)
            else:
                os.environ["ECHOES_CINEMA_P0_SEARCH_ROOTS"] = old_roots
        assert missing is None and missing_method == "automatic-search-empty"

    print(
        "CinemaRealInputAudio PASS selection=deterministic auto-search=bounded "
        "generated-files=excluded fallback=forbidden hashes=present fixture=MOCK"
    )
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
