#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from silero_speech_segments import (  # noqa: E402
    CHUNK_SAMPLES,
    SegmentationConfig,
    canonical_segments_sha256,
    speech_segments_from_probabilities,
    validate_segments,
)


class SpeechSegmentationTests(unittest.TestCase):
    def test_two_regions_are_sorted_and_padded(self) -> None:
        probabilities = [0.0] * 5 + [0.9] * 10 + [0.0] * 5 + [0.8] * 10 + [0.0] * 5
        audio_samples = len(probabilities) * CHUNK_SAMPLES
        segments = speech_segments_from_probabilities(probabilities, audio_samples)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["startSample"], 2080)
        self.assertEqual(segments[0]["endSample"], 8160)
        self.assertEqual(segments[1]["startSample"], 9760)
        self.assertEqual(segments[1]["endSample"], 15840)
        self.assertTrue(all(validate_segments(segments, audio_samples).values()))

    def test_short_burst_is_discarded(self) -> None:
        probabilities = [0.0] * 4 + [0.9] * 4 + [0.0] * 6
        audio_samples = len(probabilities) * CHUNK_SAMPLES
        self.assertEqual(speech_segments_from_probabilities(probabilities, audio_samples), [])

    def test_hysteresis_tolerates_mid_probabilities(self) -> None:
        probabilities = [0.0] * 3 + [0.9] * 8 + [0.2, 0.4, 0.4, 0.9] + [0.0] * 5
        audio_samples = len(probabilities) * CHUNK_SAMPLES
        segments = speech_segments_from_probabilities(probabilities, audio_samples)
        self.assertEqual(len(segments), 1)
        self.assertGreater(int(segments[0]["durationSamples"]), 4000)

    def test_padding_merges_adjacent_regions(self) -> None:
        config = SegmentationConfig(min_silence_ms=32, speech_pad_ms=80)
        probabilities = [0.0] * 2 + [0.9] * 8 + [0.0] * 2 + [0.9] * 8 + [0.0] * 3
        audio_samples = len(probabilities) * CHUNK_SAMPLES
        segments = speech_segments_from_probabilities(probabilities, audio_samples, config)
        self.assertEqual(len(segments), 1)

    def test_invalid_probability_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            speech_segments_from_probabilities([0.0, float("nan")], 1024)
        with self.assertRaises(ValueError):
            speech_segments_from_probabilities([0.0, 1.1], 1024)

    def test_digest_is_deterministic(self) -> None:
        probabilities = [0.0] * 5 + [0.9] * 10 + [0.0] * 5
        audio_samples = len(probabilities) * CHUNK_SAMPLES
        first = speech_segments_from_probabilities(probabilities, audio_samples)
        second = speech_segments_from_probabilities(probabilities, audio_samples)
        self.assertEqual(canonical_segments_sha256(first), canonical_segments_sha256(second))


if __name__ == "__main__":
    unittest.main()
