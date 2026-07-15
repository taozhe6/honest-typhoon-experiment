from __future__ import annotations

import unittest

import pandas as pd

from typhoon_markov.intensity_events import (
    build_five_point_windows,
    label_waveform_events,
    out_of_fold_probabilities,
    run_event_benchmark,
    storm_fold,
)


class IntensityEventTests(unittest.TestCase):
    def test_exact_five_point_window_and_event(self) -> None:
        times = pd.date_range("2024-01-01", periods=5, freq="6h", tz="UTC")
        source = pd.DataFrame(
            {
                "SID": ["TEST"] * 5,
                "SEASON": [2024] * 5,
                "NAME": ["TEST"] * 5,
                "time": times,
                "DIST2LAND": [500.0] * 5,
                "USA_WIND": [60.0, 50.0, 60.0, 50.0, 60.0],
            }
        )
        windows = build_five_point_windows(source)
        self.assertEqual(len(windows), 1)
        labelled = label_waveform_events(windows, 5.0)
        self.assertEqual(int(labelled.iloc[0]["event"]), 1)
        self.assertEqual(int(labelled.iloc[0]["past_event"]), 1)

    def test_gap_prevents_window(self) -> None:
        times = pd.to_datetime(
            [
                "2024-01-01T00:00Z",
                "2024-01-01T06:00Z",
                "2024-01-01T12:00Z",
                "2024-01-02T00:00Z",
                "2024-01-02T06:00Z",
            ],
            utc=True,
        )
        source = pd.DataFrame(
            {
                "SID": ["TEST"] * 5,
                "SEASON": [2024] * 5,
                "NAME": ["TEST"] * 5,
                "time": times,
                "DIST2LAND": [500.0] * 5,
                "USA_WIND": [60.0] * 5,
            }
        )
        self.assertTrue(build_five_point_windows(source).empty)

    def test_storm_hash_folds_are_stable(self) -> None:
        self.assertEqual(storm_fold("2024001N10000"), storm_fold("2024001N10000"))
        self.assertIn(storm_fold("2024001N10000"), range(5))

    def test_out_of_fold_probabilities_have_no_missing_values(self) -> None:
        rows = []
        found: dict[int, str] = {}
        index = 0
        while len(found) < 5:
            sid = f"S{index:04d}"
            found.setdefault(storm_fold(sid), sid)
            index += 1
        for fold, sid in found.items():
            for event in (0, 1):
                rows.append(
                    {
                        "SID": sid,
                        "fold": fold,
                        "event": event,
                        "past_event": event,
                    }
                )
        predictions, audit = out_of_fold_probabilities(pd.DataFrame(rows))
        self.assertFalse(predictions[["p_climatology", "p_persistence"]].isna().any().any())
        self.assertEqual(len(audit), 5)

    def test_benchmark_scores_storm_blocks(self) -> None:
        rows = []
        for index in range(100):
            sid = f"S{index:04d}"
            base = 40.0 + index % 3
            rows.append(
                {
                    "SID": sid,
                    "SEASON": 2024,
                    "NAME": sid,
                    "time": pd.Timestamp("2024-01-01", tz="UTC"),
                    "DIST2LAND": 500.0,
                    "fold": storm_fold(sid),
                    "v_m12_ms": base + 5.5,
                    "v_m6_ms": base,
                    "v_0_ms": base + (5.5 if index % 4 == 0 else 0.0),
                    "v_p6_ms": base - (5.5 if index % 5 == 0 else 0.0),
                    "v_p12_ms": base + (5.5 if index % 5 == 0 else 0.0),
                }
            )
        benchmark = run_event_benchmark(
            pd.DataFrame(rows),
            threshold_ms=5.0,
            subset_name="all_tropical",
            bootstrap_replicates=100,
        )
        self.assertEqual(benchmark.summary["storms"], 100)
        self.assertIn("climatology_brier", benchmark.summary["metrics"])


if __name__ == "__main__":
    unittest.main()
