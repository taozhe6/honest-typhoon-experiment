from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import pandas as pd

from typhoon_markov.intensity_events_v2 import (
    build_nine_point_windows,
    label_candidate,
    quantization_audit,
    run_temporal_benchmark,
    select_development_label,
)


def synthetic_source(storms: int = 20) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = datetime(2010, 1, 1, tzinfo=timezone.utc)
    for index in range(storms):
        event = index == 0
        winds = [80.0] * 9
        if event:
            winds[4:7] = [80.0, 70.0, 80.0]
        for step, wind in enumerate(winds):
            rows.append(
                {
                    "SID": f"S{index:03d}",
                    "SEASON": 2010,
                    "NAME": f"STORM-{index}",
                    "time": start + timedelta(days=10 * index, hours=6 * step),
                    "USA_WIND": wind,
                    "DIST2LAND": 500.0,
                }
            )
    return pd.DataFrame(rows)


class IntensityEventV2Tests(unittest.TestCase):
    def test_nine_point_window_and_waveform_label(self) -> None:
        source = synthetic_source()
        windows = build_nine_point_windows(source)
        self.assertEqual(len(windows), 20)
        labelled = label_candidate(
            windows,
            horizon_hours=12,
            threshold_ms=5.0,
            minimum_initial_intensity_ms=33.0,
            minimum_future_distance_km=0.0,
        )
        self.assertEqual(int(labelled["event"].sum()), 1)
        self.assertEqual(labelled.loc[labelled["SID"].eq("S000"), "event"].item(), 1)

    def test_development_selection_uses_frozen_tie_breakers(self) -> None:
        windows = build_nine_point_windows(synthetic_source())
        selection = select_development_label(
            windows,
            horizons=[12, 18, 24],
            thresholds_ms=[2.5, 3.0, 4.0, 5.0],
            development_seasons=(2001, 2018),
            minimum_initial_intensity_ms=33.0,
            minimum_future_distance_km=0.0,
            target_interval=(0.05, 0.15),
            target_midpoint=0.10,
            bootstrap_replicates=100,
            bootstrap_seed=17,
        )
        self.assertTrue(selection.target_achieved)
        self.assertEqual(selection.selected["horizon_hours"], 12)
        self.assertEqual(selection.selected["threshold_ms"], 5.0)
        self.assertEqual(len(selection.candidates), 12)

    def test_temporal_benchmark_has_no_storm_leakage_and_is_deterministic(self) -> None:
        rows: list[dict[str, object]] = []
        for season, prefix in ((2010, "D"), (2020, "V")):
            for index in range(20):
                rows.append(
                    {
                        "SID": f"{prefix}{index:03d}",
                        "SEASON": season,
                        "event": int(index % 5 == 0),
                        "past_event": int(index % 4 == 0),
                    }
                )
        labelled = pd.DataFrame(rows)
        first = run_temporal_benchmark(
            labelled,
            development_seasons=(2001, 2018),
            validation_seasons=(2019, 2024),
            bootstrap_replicates=200,
            bootstrap_seed=99,
        )
        second = run_temporal_benchmark(
            labelled,
            development_seasons=(2001, 2018),
            validation_seasons=(2019, 2024),
            bootstrap_replicates=200,
            bootstrap_seed=99,
        )
        self.assertEqual(first.metrics, second.metrics)
        self.assertTrue(first.metrics["nondegenerate_validation_classes"])
        self.assertEqual(set(first.development["SID"]) & set(first.validation["SID"]), set())

    def test_quantization_audit_prints_average_window_and_5kt_fraction(self) -> None:
        audit = quantization_audit(synthetic_source())
        self.assertEqual(audit["wind_average_window_minutes"], 1)
        self.assertEqual(audit["multiple_of_5kt_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()

