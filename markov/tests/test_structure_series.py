from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from typhoon_markov.structure_series import (
    build_cyclobs_structure_series,
    observed_dual_ring_intervals,
)


class StructureSeriesTests(unittest.TestCase):
    def test_ineligible_negative_pass_does_not_split_interval(self) -> None:
        frame = pd.DataFrame(
            {
                "time": pd.to_datetime(
                    ["2026-07-08T00:00Z", "2026-07-08T06:00Z", "2026-07-08T12:00Z"],
                    utc=True,
                ),
                "quality_eligible": [True, False, True],
                "dual_peak_candidate": [True, False, True],
            }
        )
        intervals = observed_dual_ring_intervals(frame)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["dual_peak_overpass_count"], 2)

    def test_eligible_negative_pass_splits_intervals(self) -> None:
        frame = pd.DataFrame(
            {
                "time": pd.to_datetime(
                    ["2026-07-08T00:00Z", "2026-07-08T06:00Z", "2026-07-08T12:00Z"],
                    utc=True,
                ),
                "quality_eligible": [True, True, True],
                "dual_peak_candidate": [True, False, True],
            }
        )
        self.assertEqual(len(observed_dual_ring_intervals(frame)), 2)

    def test_bavi_audit_supports_one_observed_interval(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "outputs"
            / "bavi_2026_cyclobs_structure_audit.json"
        )
        audit = json.loads(path.read_text(encoding="utf-8"))
        series = build_cyclobs_structure_series(audit)
        intervals = observed_dual_ring_intervals(series)
        self.assertEqual(len(series), 16)
        self.assertEqual(int(series["dual_peak_candidate"].sum()), 4)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["dual_peak_overpass_count"], 4)


if __name__ == "__main__":
    unittest.main()
