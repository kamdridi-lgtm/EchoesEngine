#!/usr/bin/env python3
"""Generate a deterministic PCM WAV used to prove the local Windows runtime."""
from __future__ import annotations

import argparse
import hashlib
import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16_000
DURATION_SECONDS = 6


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    samples: list[int] = []
    for index in range(SAMPLE_RATE * DURATION_SECONDS):
        second = index / SAMPLE_RATE
        if 1.0 <= second < 3.0 or 4.0 <= second < 5.0:
            envelope = min(1.0, (second % 1.0) * 8.0, (1.0 - (second % 1.0)) * 8.0)
            value = 0.18 * envelope * math.sin(2.0 * math.pi * 220.0 * second)
        else:
            value = 0.0
        samples.append(max(-32768, min(32767, int(round(value * 32767.0)))))

    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"".join(struct.pack("<h", value) for value in samples))

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"EchoesLocalRuntimeSmokeWav PASS path={output.as_posix()} sha256={digest} seconds={DURATION_SECONDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
