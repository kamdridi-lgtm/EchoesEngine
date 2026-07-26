from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from song_activity_timeline import (  # noqa: E402
    NON_SPEECH,
    SPEECH,
    ActivitySpan,
    build_activity_partition,
    canonical_digest,
    summarize,
    validate_partition,
)


class SongActivityTimelineTests(unittest.TestCase):
    def test_partition_covers_audio_without_gaps(self) -> None:
        timeline = build_activity_partition(1000, [(100, 200), (400, 700)])
        self.assertEqual(
            timeline,
            [
                ActivitySpan(NON_SPEECH, 0, 100),
                ActivitySpan(SPEECH, 100, 200),
                ActivitySpan(NON_SPEECH, 200, 400),
                ActivitySpan(SPEECH, 400, 700),
                ActivitySpan(NON_SPEECH, 700, 1000),
            ],
        )
        self.assertTrue(all(validate_partition(timeline, 1000).values()))

    def test_no_speech_becomes_one_non_speech_span(self) -> None:
        self.assertEqual(build_activity_partition(500, []), [ActivitySpan(NON_SPEECH, 0, 500)])

    def test_full_speech_becomes_one_speech_span(self) -> None:
        self.assertEqual(build_activity_partition(500, [(0, 500)]), [ActivitySpan(SPEECH, 0, 500)])

    def test_overlapping_speech_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_activity_partition(1000, [(100, 500), (400, 600)])

    def test_out_of_bounds_speech_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_activity_partition(1000, [(100, 1001)])

    def test_digest_is_deterministic_and_label_sensitive(self) -> None:
        timeline = build_activity_partition(1000, [(100, 200)])
        first = canonical_digest(timeline, 16000, 1000)
        second = canonical_digest(timeline, 16000, 1000)
        changed = canonical_digest([ActivitySpan(SPEECH, 0, 1000)], 16000, 1000)
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_summary_accounting_is_exact(self) -> None:
        timeline = build_activity_partition(16000, [(4000, 12000)])
        summary = summarize(timeline, 16000, 16000)
        self.assertEqual(summary["speechSamples"], 8000)
        self.assertEqual(summary["nonSpeechSamples"], 8000)
        self.assertEqual(summary["speechCoverage"], 0.5)
        self.assertEqual(summary["nonSpeechCoverage"], 0.5)


if __name__ == "__main__":
    unittest.main()
