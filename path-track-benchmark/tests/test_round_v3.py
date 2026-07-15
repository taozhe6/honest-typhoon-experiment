from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from path_benchmark.core import ForecastPoint, TruthPoint
from path_benchmark.round_v3 import (
    TripleTrackRow,
    independence_diagnostics,
    strict_triple_pair,
    summarize_triple_rows,
)


class RoundV3Tests(unittest.TestCase):
    def test_strict_triple_pair_requires_all_three_aids(self) -> None:
        cycle = datetime(2024, 9, 1, tzinfo=timezone.utc)
        valid_time = cycle + timedelta(hours=24)
        truth = {
            ("WP122024", valid_time): TruthPoint(
                "WP122024", valid_time, 20.0, 140.0
            )
        }
        forecasts = [
            ForecastPoint("WP122024", cycle, "CMC", 24, 20.1, 140.0),
            ForecastPoint("WP122024", cycle, "NGX", 24, 19.9, 140.0),
        ]
        self.assertEqual(
            strict_triple_pair(forecasts, truth, {"WP122024": "YAGI"}), []
        )
        forecasts.append(
            ForecastPoint("WP122024", cycle, "UKM", 24, 20.0, 140.2)
        )
        rows = strict_triple_pair(forecasts, truth, {"WP122024": "YAGI"})
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].local_eq2_error_km, 0.0, places=6)
        self.assertGreater(rows[0].ukm_error_km, 0.0)

    def test_summary_and_joint_neff_bootstrap_are_deterministic(self) -> None:
        cycle = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows: list[TripleTrackRow] = []
        permutation = (5, 1, 9, 2, 11, 4, 8, 0, 10, 3, 7, 6)
        for index in range(12):
            for lead in (24, 48):
                common = float(index + lead / 24)
                local = float(index + 1)
                ukm = float(permutation[index] + 1)
                rows.append(
                    TripleTrackRow(
                        atcf_id=f"WP{index + 1:02d}2024",
                        storm_name=f"STORM-{index + 1}",
                        cycle_utc=cycle,
                        valid_time_utc=cycle + timedelta(hours=lead),
                        lead_hours=lead,
                        truth_latitude=0.0,
                        truth_longitude=0.0,
                        cmc_latitude=0.0,
                        cmc_longitude=0.0,
                        ngx_latitude=0.0,
                        ngx_longitude=0.0,
                        ukm_latitude=0.0,
                        ukm_longitude=0.0,
                        local_eq2_latitude=0.0,
                        local_eq2_longitude=0.0,
                        cmc_error_km=common,
                        ngx_error_km=2.0 * common,
                        ukm_error_km=ukm,
                        local_eq2_error_km=local,
                    )
                )

        summary = summarize_triple_rows(rows, [24, 48], 100, 99)
        self.assertEqual(
            {item["stream"] for item in summary},
            {
                "CMC",
                "NGX",
                "UKM",
                "LOCAL_EQ2_CMC_NGX",
                "LOCAL_EQ2_MINUS_UKM",
            },
        )
        first = independence_diagnostics(rows, 200, 99)
        second = independence_diagnostics(rows, 200, 99)
        self.assertEqual(first, second)
        primary = first["primary"]
        self.assertAlmostEqual(primary["neff_cmc_ngx"], 1.0)
        self.assertGreater(primary["neff_local_eq2_ukm"], 1.2)
        self.assertGreater(primary["delta_neff"], 0.0)


if __name__ == "__main__":
    unittest.main()

