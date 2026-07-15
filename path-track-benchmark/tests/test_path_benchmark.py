from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from path_benchmark.core import (
    correlation_diagnostics,
    DataConflictError,
    ForecastPoint,
    leave_one_storm_out_intervals,
    PairedTrackRow,
    parse_adeck,
    parse_atcf_coordinate,
    read_ibtracs_truth,
    spherical_consensus,
    strict_pair,
    summarize_rows,
    summarize_rows_v2,
    track_error_km,
)


class PathBenchmarkTests(unittest.TestCase):
    def test_coordinate_parser_handles_dateline_and_hemispheres(self) -> None:
        self.assertEqual(parse_atcf_coordinate("123N"), 12.3)
        self.assertEqual(parse_atcf_coordinate("045S"), -4.5)
        self.assertAlmostEqual(parse_atcf_coordinate("1799E"), 179.9)
        self.assertAlmostEqual(parse_atcf_coordinate("1799W"), -179.9)

    def test_identical_wind_radius_rows_are_deduplicated(self) -> None:
        row = "WP, 12, 2024090100, 03, CMC, 024, 200N, 1400E, 40, 990"
        points = parse_adeck(
            row + "\n" + row, "WP122024", {"CMC"}, {24}
        )
        self.assertEqual(len(points), 1)

    def test_conflicting_duplicate_positions_fail(self) -> None:
        text = "\n".join(
            [
                "WP, 12, 2024090100, 03, CMC, 024, 200N, 1400E",
                "WP, 12, 2024090100, 03, CMC, 024, 201N, 1400E",
            ]
        )
        with self.assertRaises(DataConflictError):
            parse_adeck(text, "WP122024", {"CMC"}, {24})

    def test_wgs84_equatorial_degree(self) -> None:
        self.assertAlmostEqual(track_error_km(0.0, 0.0, 0.0, 1.0), 111.319, 3)

    def test_spherical_consensus_handles_dateline(self) -> None:
        latitude, longitude = spherical_consensus(
            [(0.0, 179.0), (0.0, -179.0)], [0.5, 0.5]
        )
        self.assertAlmostEqual(latitude, 0.0)
        self.assertAlmostEqual(abs(longitude), 180.0)

    def test_truth_reader_and_strict_pair_use_exact_valid_time(self) -> None:
        csv_text = "\n".join(
            [
                "USA_ATCF_ID,ISO_TIME,USA_LAT,USA_LON",
                ",,degrees_north,degrees_east",
                "WP122024,2024-09-02 00:00:00,20.0,140.0",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truth.csv"
            path.write_text(csv_text, encoding="utf-8")
            truth = read_ibtracs_truth(path, {"WP122024"})
        cycle = datetime(2024, 9, 1, tzinfo=timezone.utc)
        forecasts = [
            ForecastPoint("WP122024", cycle, "CMC", 24, 20.1, 140.0),
            ForecastPoint("WP122024", cycle, "NGX", 24, 19.9, 140.0),
        ]
        rows = strict_pair(forecasts, truth, {"WP122024": "YAGI"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].valid_time_utc.hour, 0)
        self.assertIsNotNone(rows[0].dyc2_error_km)

    def test_storm_block_summary_is_deterministic(self) -> None:
        cycle = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index, storm in enumerate(("WP012024", "WP022024"), start=1):
            rows.append(
                PairedTrackRow(
                    storm,
                    storm,
                    cycle,
                    cycle,
                    24,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    10.0 * index,
                    20.0 * index,
                )
            )
        first = summarize_rows(rows, [24], 100, 123)
        second = summarize_rows(rows, [24], 100, 123)
        self.assertEqual(first, second)
        self.assertEqual({item["aid"] for item in first}, {"CMC", "NGX", "CMC_MINUS_NGX"})

    def test_v2_summary_correlation_and_cross_validation(self) -> None:
        cycle = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(12):
            error = float(index + 1)
            rows.append(
                PairedTrackRow(
                    atcf_id=f"WP{index + 1:02d}2024",
                    storm_name=f"STORM-{index + 1}",
                    cycle_utc=cycle,
                    valid_time_utc=cycle,
                    lead_hours=24,
                    truth_latitude=0.0,
                    truth_longitude=0.0,
                    cmc_latitude=0.0,
                    cmc_longitude=0.0,
                    ngx_latitude=0.0,
                    ngx_longitude=0.0,
                    cmc_error_km=error,
                    ngx_error_km=error,
                    dyc2_latitude=0.0,
                    dyc2_longitude=0.0,
                    dyc2_error_km=error,
                )
            )
        summary = summarize_rows_v2(rows, [24], 100, 123)
        self.assertEqual(
            {item["aid"] for item in summary},
            {"CMC", "NGX", "DYC2", "DYC2_MINUS_CMC", "DYC2_MINUS_NGX"},
        )
        correlation = correlation_diagnostics(rows, [24], 100, 123)
        self.assertAlmostEqual(correlation["primary"]["rho"], 1.0)
        self.assertAlmostEqual(correlation["primary"]["neff"], 1.0)
        intervals, evaluations = leave_one_storm_out_intervals(
            rows, [24], [0.8], 10, 100, 123
        )
        self.assertEqual(intervals[0]["status"], "estimated")
        self.assertEqual(intervals[0]["training_storm_count_per_fold"], 11)
        self.assertEqual(len(evaluations), 12)


if __name__ == "__main__":
    unittest.main()
