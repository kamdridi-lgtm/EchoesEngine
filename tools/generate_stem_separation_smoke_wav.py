#!/usr/bin/env python3
"""Generate a short deterministic stereo music-like WAV for real HTDemucs CI inference."""
from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=2.0)
    args = parser.parse_args()

    sample_rate = 44100
    frames = max(1, int(round(sample_rate * args.duration)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        block = bytearray()
        for index in range(frames):
            t = index / sample_rate
            beat = 1.0 if (index % (sample_rate // 4)) < 900 else 0.0
            bass = 0.22 * math.sin(2.0 * math.pi * 82.41 * t)
            chord = 0.11 * math.sin(2.0 * math.pi * 329.63 * t) + 0.08 * math.sin(2.0 * math.pi * 493.88 * t)
            voice_like = 0.10 * math.sin(2.0 * math.pi * (220.0 + 25.0 * math.sin(2.0 * math.pi * 4.5 * t)) * t)
            click = 0.18 * beat * math.exp(-20.0 * ((index % (sample_rate // 4)) / sample_rate))
            left = max(-0.95, min(0.95, bass + chord + voice_like + click))
            right = max(-0.95, min(0.95, bass + 0.92 * chord + 1.03 * voice_like + click))
            block.extend(struct.pack("<hh", int(left * 32767), int(right * 32767)))
        wav.writeframes(block)
    print(f"StemSmokeWav PASS path={args.output} frames={frames} duration={frames / sample_rate:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
